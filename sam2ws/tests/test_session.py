import io
import tempfile
import unittest

from PIL import Image


def _jpeg(w=8, h=8):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


JPEG = _jpeg()


class FakePredictor:
    def __init__(self):
        self.inits = []
        self.points = []

    def init_state(self, video_path, **kw):
        import torch
        self.inits.append(video_path)
        return {"fake": True, "video_path": video_path,
                "images": torch.zeros(0, 3, 1024, 1024),
                "num_frames": 0, "device": torch.device("cpu"),
                "offload_video_to_cpu": False}

    def add_new_points_or_box(self, state, frame_idx, obj_id, points, labels):
        self.points.append((frame_idx, obj_id, list(points), list(labels)))
        return state, [0], [True]

    def propagate_in_video(self, state, start_frame_idx=None,
                           max_frame_num_to_track=None):
        return iter([])

    def remove_object(self, state, obj_id, strict=False, need_output=True):
        return state, [0], [True]

    def reset_state(self, state):
        return state


class SessionTest(unittest.TestCase):
    def test_slide_reinits_and_evicts_old_clicks(self):
        from sam2ws import protocol
        from sam2ws.session import TrackingSession
        with tempfile.TemporaryDirectory() as d:
            s = TrackingSession(FakePredictor(), d, window=4, memory=7)
            for _ in range(4):
                s.ingest(JPEG, 8, 8)
            self.assertEqual(len(s.predictor.inits), 1)
            s.click(0, 1, 1, 1, 1)
            # window-relative remap: global frame 0 is index 0 pre-slide
            self.assertEqual(s.predictor.points[-1][0], 0)
            s.ingest(JPEG, 8, 8)
            self.assertEqual(len(s.predictor.inits), 2)
            with self.assertRaises(protocol.ProtocolError) as cm:
                s.click(0, 1, 1, 1, 1)
            self.assertEqual(cm.exception.code, "evicted")
            # live frame still clickable, remapped to window index 3
            s.click(4, 2, 2, 1, 1)
            self.assertEqual(s.predictor.points[-1][0], 3)

    def test_masks_for_latest_parses_yield(self):
        import torch
        from sam2ws.session import TrackingSession

        class YieldingFake(FakePredictor):
            def propagate_in_video(self, state, start_frame_idx=None,
                                   max_frame_num_to_track=None):
                yield 2, [1], torch.zeros(1, 1, 8, 8) + 5.0

        with tempfile.TemporaryDirectory() as d:
            s = TrackingSession(YieldingFake(), d, window=4, memory=7)
            for _ in range(3):
                s.ingest(JPEG, 8, 8)
            s.click(0, 1, 1, 1, 1)
            out = s.masks_for_latest()
            self.assertEqual(len(out), 1)
            oid, mask, scale = out[0]
            self.assertEqual(oid, 1)
            self.assertTrue(mask.all())
            self.assertAlmostEqual(scale, 1.0)
