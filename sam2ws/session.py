import os

import numpy as np
import torch
from PIL import Image

from sam2ws import protocol

_IMG_MEAN = (0.485, 0.456, 0.406)
_IMG_STD = (0.229, 0.224, 0.225)
_IMG_SIZE = 1024


class TrackingSession:
    """Windowed online adapter over SAM2VideoPredictor (public API only).

    Keeps at most `window` JPEGs in `work_dir` and re-inits predictor state
    from that window whenever it slides, re-applying stored clicks with
    remapped frame indices. Frame numbering is global and monotonic;
    predictor calls use window-relative indices (global - base).
    """

    def __init__(self, predictor, work_dir, window=16, memory=7):
        self.predictor = predictor
        self.work_dir = work_dir
        self.window = window
        self.memory = memory  # documented; upstream bank is num_maskmem=7
        os.makedirs(work_dir, exist_ok=True)
        self._frames = []  # absolute jpeg paths, oldest first, contiguous idx
        self._clicks = []  # (global_idx, x, y, label, obj_id)
        self._base = 0  # global index of _frames[0]
        self._state = None
        self._last_wh = (0, 0)

    def _boot(self):
        assert self._frames, "boot needs at least one frame"
        self._state = self.predictor.init_state(video_path=self.work_dir)
        for (fi, x, y, lab, oid) in self._clicks:
            self.predictor.add_new_points_or_box(
                self._state, fi - self._base, oid,
                points=[[x, y]], labels=[lab],
            )

    def _encode_frame(self, jpeg_bytes):
        """Mirror upstream _load_img_as_tensor + normalize, on model device."""
        import io
        img_pil = Image.open(io.BytesIO(jpeg_bytes))
        img_np = np.array(img_pil.convert("RGB").resize((_IMG_SIZE, _IMG_SIZE)))
        img = torch.from_numpy(img_np / 255.0).permute(2, 0, 1).float()
        mean = torch.tensor(_IMG_MEAN).view(3, 1, 1)
        std = torch.tensor(_IMG_STD).view(3, 1, 1)
        img = (img - mean) / std
        return img.to(self._state["device"], non_blocking=True)

    def _try_append(self, jpeg_bytes):
        """Fast path: cat one frame onto state images. Returns False to
        signal the caller must re-init from the disk window instead."""
        try:
            if self._state.get("offload_video_to_cpu", False):
                return False
            imgs = self._state["images"]
            if not isinstance(imgs, torch.Tensor):
                return False
            frame = self._encode_frame(jpeg_bytes)
            if (tuple(frame.shape) != tuple(imgs.shape[1:])
                    or frame.dtype != imgs.dtype
                    or frame.device != imgs.device):
                return False
            self._state["images"] = torch.cat([imgs, frame.unsqueeze(0)], dim=0)
            self._state["num_frames"] = int(self._state["num_frames"]) + 1
            return True
        except Exception:
            return False

    def ingest(self, jpeg_bytes, w, h):
        self._last_wh = (w, h)
        idx = self._base + len(self._frames)
        path = os.path.join(self.work_dir, f"{idx:06d}.jpg")
        with open(path, "wb") as f:
            f.write(jpeg_bytes)
        self._frames.append(path)
        if self._state is None:
            self._boot()
        elif len(self._frames) > self.window:
            drop = len(self._frames) - self.window
            for p in self._frames[:drop]:
                if os.path.exists(p):
                    os.remove(p)
            self._frames = self._frames[drop:]
            self._base += drop
            self._clicks = [(fi, x, y, l, o) for (fi, x, y, l, o) in self._clicks
                            if fi >= self._base]
            self._boot()
        elif not self._try_append(jpeg_bytes):
            self._boot()
        return self._base + len(self._frames) - 1

    def _check_live(self, frame_idx):
        if frame_idx < self._base or frame_idx >= self._base + len(self._frames):
            raise protocol.ProtocolError("evicted", f"frame {frame_idx} not retained")

    def click(self, frame_idx, x, y, label, obj_id):
        self._check_live(frame_idx)
        self.predictor.add_new_points_or_box(
            self._state, frame_idx - self._base, obj_id,
            points=[[x, y]], labels=[label],
        )
        self._clicks.append((frame_idx, x, y, label, obj_id))

    def remove(self, obj_ids, all_flag):
        if all_flag:
            self._clicks = []
            self._boot()
            return
        gone = set(obj_ids)
        for oid in obj_ids:
            self.predictor.remove_object(self._state, oid)
        self._clicks = [(fi, x, y, l, o) for (fi, x, y, l, o) in self._clicks
                        if o not in gone]

    def masks_for_latest(self):
        latest = self._base + len(self._frames) - 1
        start = self._clicks[0][0] if self._clicks else latest
        w, h = self._last_wh
        out = []
        for frame_idx, obj_ids, video_res_masks in self.predictor.propagate_in_video(
            self._state, start_frame_idx=start - self._base,
            max_frame_num_to_track=self.window,
        ):
            if frame_idx != latest - self._base:
                continue
            arr = video_res_masks.detach().cpu()
            for oid, logits in zip(obj_ids, arr):
                mask = (logits.squeeze().numpy() > 0.0)
                assert mask.ndim == 2, mask.shape
                mh, mw = mask.shape
                sx, sy = mw / max(1, w), mh / max(1, h)
                assert abs(sx - sy) < 0.05, (sx, sy)
                out.append((oid, mask, (sx + sy) / 2))
        return out

    def reset(self):
        self._clicks = []
        if self._frames:
            self._boot()
