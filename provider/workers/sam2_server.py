"""Loopback SAM2 inspect worker (port 8771).

Serves the contract in `workers/sam2_client.py`:

    POST /inspect  {frame_id, jpeg_b64, target, phrase?}
                   -> {frame_id, objects: [{u0, v0, u1, v1, score, mask_png_b64?}]}

`coordinator/jobs.handle_inspect` consumes this: it sorts the objects by
`float(score)` descending, keeps the top 3, and reads `u0`, `v0`, `u1`, `v1` and
`score` on each (it will KeyError if any is missing). All four coordinates are
normalised image-space floats in [0, 1], because the Quest consumes them as an
`image_box`.

Two notes on how this differs from the sibling generation worker:

* The frame arrives WITH the request body, so this worker does not need the SAM2
  bridge's frame history -- it segments the JPEG it was handed.
* `sam2ws.segment.Segmenter` is deliberately not used here. Its `segment_frame`
  returns `(obj_id, mask)` and drops SAM2's own confidence scores, but the
  coordinator ranks candidates by score. So this worker drives
  `SAM2ImagePredictor` directly, keeping the same click convention, and preserves
  the scores. Multi-mask output is requested so a single ambiguous click yields
  ranked candidates rather than one arbitrary winner.

`mask_png_b64` is an 8-bit single-channel PNG where non-zero means object -- the
convention `workers/gen_server.py:mask_from_png` decodes.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SAM2_PORT = 8771
MAX_OBJECTS = 3
MAX_JPEG_BYTES = 8 * 1024 * 1024
# Below this coverage a candidate is noise (a stray few pixels); above it the
# "object" is the whole frame and is useless as a target.
MIN_COVERAGE = 0.002


def clamp01(value: Any) -> float | None:
    """Coerce a JSON number to [0, 1], or None if it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:  # NaN
        return None
    return min(1.0, max(0.0, number))


def target_to_clicks(target: Any, width: int, height: int) -> list[dict[str, int]]:
    """Map an image-space target onto SAM2 point prompts.

    The target vocabulary is fixed by `coordinator/listings.py`: capture_hint,
    pointing, image_point, image_box. Anything without usable coordinates --
    including capture_hint, which is what the model sends for "in front of me" --
    falls back to a centre click.
    """
    centre = [{"x": width // 2, "y": height // 2, "label": 1, "obj_id": 1}]
    if not isinstance(target, dict):
        return centre

    if target.get("type") in ("image_point", "pointing"):
        u = clamp01(target.get("u"))
        v = clamp01(target.get("v"))
        if u is None or v is None:
            return centre
        return [{"x": int(u * (width - 1)), "y": int(v * (height - 1)), "label": 1, "obj_id": 1}]

    if target.get("type") == "image_box":
        u0 = clamp01(target.get("u0"))
        v0 = clamp01(target.get("v0"))
        u1 = clamp01(target.get("u1"))
        v1 = clamp01(target.get("v1"))
        if None in (u0, v0, u1, v1):
            return centre
        return [
            {
                "x": int((u0 + u1) / 2 * (width - 1)),
                "y": int((v0 + v1) / 2 * (height - 1)),
                "label": 1,
                "obj_id": 1,
            }
        ]

    return centre


def mask_bbox_norm(mask) -> tuple[float, float, float, float] | None:
    """Normalised (u0, v0, u1, v1) bbox of a boolean mask, or None if empty."""
    import numpy as np

    height, width = mask.shape
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return (
        float(xs.min()) / width,
        float(ys.min()) / height,
        float(xs.max() + 1) / width,
        float(ys.max() + 1) / height,
    )


def encode_mask_png(mask) -> str:
    """Encode a boolean mask as base64 8-bit PNG (non-zero = object)."""
    import numpy as np
    from PIL import Image

    data = (np.asarray(mask) > 0).astype(np.uint8) * 255
    buffer = io.BytesIO()
    Image.fromarray(data, mode="L").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def decode_image(value: Any, *, limit: int = MAX_JPEG_BYTES) -> bytes | None:
    if not isinstance(value, str) or not value or len(value) > limit * 2:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


class InspectWorker:
    """Warm SAM2 predictor; stateless per request."""

    def __init__(self, predictor) -> None:
        self._predictor = predictor

    def inspect(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        frame_id = body.get("frame_id")
        jpeg = decode_image(body.get("jpeg_b64"))
        if jpeg is None:
            return 400, {"frame_id": frame_id, "objects": [], "error": "missing_image"}

        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as img:
            rgb = img.convert("RGB")
            width, height = rgb.size
            array = np.array(rgb)

        clicks = target_to_clicks(body.get("target"), width, height)
        coords = np.array([[c["x"], c["y"]] for c in clicks], dtype=np.float32)
        labels = np.array([c["label"] for c in clicks], dtype=np.int32)

        self._predictor.set_image(array)
        masks, scores, _ = self._predictor.predict(
            point_coords=coords,
            point_labels=labels,
            multimask_output=True,
        )

        objects: list[dict[str, Any]] = []
        for mask, score in zip(np.asarray(masks), np.asarray(scores)):
            bool_mask = mask.astype(bool)
            coverage = float(bool_mask.mean())
            if coverage < MIN_COVERAGE:
                continue
            bbox = mask_bbox_norm(bool_mask)
            if bbox is None:
                continue
            u0, v0, u1, v1 = bbox
            objects.append({
                "u0": u0,
                "v0": v0,
                "u1": u1,
                "v1": v1,
                "score": float(score),
                "mask_png_b64": encode_mask_png(bool_mask),
            })

        objects.sort(key=lambda o: o["score"], reverse=True)
        objects = objects[:MAX_OBJECTS]
        logger.info(
            "inspect frame=%s target=%s objects=%d",
            frame_id, (body.get("target") or {}).get("type"), len(objects),
        )
        return 200, {"frame_id": frame_id, "objects": objects}


class _InspectHandler(BaseHTTPRequestHandler):
    worker: InspectWorker

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.debug(format, *args)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/inspect":
            self._write_json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_JPEG_BYTES * 2:
            self._write_json(413, {"error": "bad_length"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._write_json(400, {"error": "bad_json"})
            return
        if not isinstance(body, dict):
            self._write_json(400, {"error": "bad_body"})
            return
        try:
            status, payload = self.worker.inspect(body)
        except Exception as exc:  # noqa: BLE001 - a failed inspect is not a crash
            logger.exception("inspect failed")
            self._write_json(500, {"frame_id": body.get("frame_id"), "objects": [],
                                   "error": type(exc).__name__})
            return
        self._write_json(status, payload)


def start_inspect_server(host: str, port: int, worker: InspectWorker) -> ThreadingHTTPServer:
    handler = type("BoundInspectHandler", (_InspectHandler,), {"worker": worker})
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="inspect-http")
    thread.start()
    return server


def build_predictor(*, checkpoint: Path, config: str, device: str = "cuda:0"):
    """Load SAM2 and return a warm predictor."""
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA unavailable: check LD_LIBRARY_PATH includes /run/opengl-driver/lib"
        )
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    model = build_sam2(config, str(checkpoint), device=device)
    return SAM2ImagePredictor(model)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    checkpoint = Path(os.environ.get(
        "SAM2_CHECKPOINT",
        "/home/nate/Projects/omni-3d-gen/sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
    ))
    config = os.environ.get("SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_t.yaml")
    host = os.environ.get("SAM2_HOST", "127.0.0.1")
    port = int(os.environ.get("SAM2_PORT", str(SAM2_PORT)))

    if not checkpoint.is_file():
        logger.error("SAM2 checkpoint missing: %s", checkpoint)
        return 2

    try:
        predictor = build_predictor(checkpoint=checkpoint, config=config)
    except Exception:
        logger.exception("model load failed; refusing to serve")
        return 3

    worker = InspectWorker(predictor)
    server = start_inspect_server(host, port, worker)
    logger.info("inspect worker on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
