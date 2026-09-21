"""Loopback image-to-3D generation worker (port 8772).

Serves the contract in `workers/gen_client.py`:

    POST /jobs        {job_id, frame_id, jpeg_b64, prompt?, mask_png_b64?}
                      -> job object; 409 when a job is already running
    GET  /jobs/{id}   -> job object incl. "status" and per-job timings

Two contract details drive the whole design, both from the coordinator side:

1. READINESS IS THE ARTIFACT FILE EXISTING, not the status string. The coordinator
   (`coordinator/jobs.poll_queued_jobs`) marks a job ready when
   `artifact_path(artifact_root, job_id)` exists, because that file is exactly what
   the artifact HTTP server checks before serving. `queue_job` sends no output
   path, so this worker owns the write location: it must land the GLB at
   `<artifact_root>/<job_id>.glb`. Writes go to a temp file and are moved into place
   with `os.replace` so the coordinator can never observe a half-written GLB.

2. The status is consulted ONLY to learn about a terminal failure. `"failed"` is the
   one string that settles a job; `"queued"`/`"running"` keep the coordinator
   waiting. This worker never reports `"ready"` in place of the file.

`prompt` is the item name (e.g. "oak side table"). It is a LABEL. SAM2 is not
text-prompted and this worker does not attempt text-guided segmentation; the prompt
is used for logging and job bookkeeping only.

When no mask is supplied the subject is assumed to be centred: the worker takes a
centre click, exactly as the reference implementation does, and refuses the job if
that click clearly missed or swallowed the whole frame. That is the demo's real
gesture (hold the item or its photo centred in view), not a hidden assumption.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GEN_PORT = 8772

# Job vocabulary, shared with coordinator/jobs.py. Only WORKER_FAILED is terminal
# in the failure direction.
WORKER_QUEUED = "queued"
WORKER_RUNNING = "running"
WORKER_READY = "ready"
WORKER_FAILED = "failed"

# Spec §3: a generation that exceeds this is reported failed so the coordinator can
# settle the job instead of waiting forever.
GENERATION_TIMEOUT_S = 90.0

# Centre-click sanity band, from the reference implementation: below MIN the click
# missed the subject, above MAX it took the whole frame.
MIN_MASK_COVERAGE = 0.02
MAX_MASK_COVERAGE = 0.8

# TripoSR settings matching the measured PERF.md baseline.
MC_RESOLUTION = 256
FOREGROUND_RATIO = 0.85
RENDERER_CHUNK = 8192

MAX_JPEG_BYTES = 8 * 1024 * 1024
MAX_MASK_BYTES = 4 * 1024 * 1024


def default_artifact_root() -> Path:
    """The coordinator's DEFAULT_ARTIFACT_ROOT, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent / "artifacts" / "generated"


def decode_b64(value: str | None, *, limit: int) -> bytes | None:
    """Decode a base64 payload, rejecting anything oversized or malformed."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) > limit * 2:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def mask_from_png(png_bytes: bytes):
    """Decode a single-channel mask PNG into a 2D bool array.

    Convention with the inspect worker: non-zero pixel = object.
    """
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as img:
        arr = np.asarray(img.convert("L"))
    return arr > 0


def mask_coverage_reason(mean: float) -> str | None:
    """Return a failure reason if a centre-click mask is implausible, else None."""
    if mean < MIN_MASK_COVERAGE:
        return "click_missed"
    if mean > MAX_MASK_COVERAGE:
        return "click_whole_frame"
    return None


class Pipeline:
    """Warm SAM2 + TripoSR. Loaded once, reused for every job.

    Both models stay resident: measured peaks are 0.63 GB (SAM2) and 2.49 GB
    (TripoSR), so co-residency fits the 8 GB card with headroom.
    """

    def __init__(self, *, sam2_checkpoint: Path, config: str, device: str = "cuda:0") -> None:
        self.device = device
        self._config = config
        self._sam2_checkpoint = sam2_checkpoint
        self._segmenter = None
        self._tripo = None

    def load(self) -> None:
        """Load both models. Raises if the GPU or the checkpoints are unusable."""
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA unavailable: check LD_LIBRARY_PATH includes /run/opengl-driver/lib "
                "(without it torch silently reports no GPU)"
            )

        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        from sam2ws.segment import Segmenter

        started = time.monotonic()
        sam_model = build_sam2(self._config, str(self._sam2_checkpoint), device=self.device)
        self._segmenter = Segmenter(SAM2ImagePredictor(sam_model))
        logger.info("SAM2 loaded in %.3fs", time.monotonic() - started)

        from huggingface_hub import hf_hub_download
        from omegaconf import OmegaConf
        from tripo_remap import remap_state_dict
        from tsr.system import TSR

        started = time.monotonic()
        cfg = OmegaConf.load(hf_hub_download("stabilityai/TripoSR", "config.yaml"))
        OmegaConf.resolve(cfg)
        tripo = TSR(cfg)
        ckpt = torch.load(
            hf_hub_download("stabilityai/TripoSR", "model.ckpt"),
            map_location="cpu",
            weights_only=True,
        )
        tripo.load_state_dict(remap_state_dict(ckpt), strict=True)
        tripo.renderer.set_chunk_size(RENDERER_CHUNK)
        tripo.to(self.device)
        self._tripo = tripo
        logger.info("TripoSR loaded in %.3fs", time.monotonic() - started)

    def segment_centre(self, jpeg: bytes) -> tuple[Any, float, float]:
        """Centre-click segment. Returns (mask, coverage, sam_ms)."""
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as img:
            width, height = img.size
        started = time.monotonic()
        out = self._segmenter.segment_frame(
            jpeg, [{"x": width // 2, "y": height // 2, "label": 1, "obj_id": 1}]
        )
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if not out:
            raise RuntimeError("sam2 returned no mask")
        _obj_id, mask = out[0]
        return mask, float(mask.mean()), elapsed_ms

    def generate(self, jpeg: bytes, mask_png: bytes | None) -> tuple[Any, dict[str, float]]:
        """Run the pipeline and return (trimesh mesh, timings)."""
        import numpy as np
        import torch
        from PIL import Image
        from sam2ws.cutout import cutout_rgba
        from tsr.utils import resize_foreground

        timings: dict[str, float] = {}

        if mask_png is not None:
            mask = mask_from_png(mask_png)
            timings["sam_ms"] = 0.0
        else:
            mask, coverage, sam_ms = self.segment_centre(jpeg)
            timings["sam_ms"] = round(sam_ms, 1)
            reason = mask_coverage_reason(coverage)
            if reason is not None:
                raise MaskRejected(reason, coverage)

        rgba = cutout_rgba(jpeg, mask, margin=0.05)

        image = resize_foreground(rgba, FOREGROUND_RATIO)
        image = np.array(image).astype(np.float32) / 255.0
        image = image[:, :, :3] * image[:, :, 3:4] + (1 - image[:, :, 3:4]) * 0.5
        image = Image.fromarray((image * 255.0).astype(np.uint8))

        started = time.monotonic()
        with torch.no_grad():
            scene_codes = self._tripo([image], device=self.device)
        meshes = self._tripo.extract_mesh(scene_codes, True, resolution=MC_RESOLUTION)
        timings["tripo_ms"] = round((time.monotonic() - started) * 1000.0, 1)
        timings["verts"] = float(len(meshes[0].vertices))
        return meshes[0], timings


class MaskRejected(RuntimeError):
    """A centre-click mask was implausible; the job fails with the given reason."""

    def __init__(self, reason: str, coverage: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.coverage = coverage


class GenerationWorker:
    """One job at a time. The GPU is the resource being serialised."""

    def __init__(self, artifact_root: Path, pipeline: Pipeline,
                 *, timeout_s: float = GENERATION_TIMEOUT_S) -> None:
        self.artifact_root = artifact_root
        self._pipeline = pipeline
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job: str | None = None

    def submit(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Accept or refuse one job. Returns (http_status, payload)."""
        job_id = body.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return 400, {"error": "missing_job_id"}
        if body.get("frame_id") is not None and not isinstance(body.get("frame_id"), str):
            return 400, {"error": "bad_frame_id"}

        jpeg = decode_b64(body.get("jpeg_b64"), limit=MAX_JPEG_BYTES)
        if jpeg is None:
            return 400, {"error": "missing_image"}
        mask_png = decode_b64(body.get("mask_png_b64"), limit=MAX_MASK_BYTES)

        with self._lock:
            if self._active_job is not None:
                return 409, {"error": "busy", "job_id": self._active_job}
            if job_id in self._jobs:
                return 409, {"error": "duplicate_job", "job_id": job_id}
            self._active_job = job_id
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": WORKER_QUEUED,
                "prompt": body.get("prompt"),
                "started_at": time.monotonic(),
                "timings": {},
            }

        thread = threading.Thread(
            target=self._run, args=(job_id, jpeg, mask_png), daemon=True,
            name=f"gen-{job_id[:8]}",
        )
        thread.start()
        return 200, dict(self._jobs[job_id])

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            out = dict(job)
            # A job still running past its budget is reported failed, so the
            # coordinator settles it rather than waiting forever. The thread keeps
            # its GPU work, but its result is discarded on completion.
            if job["status"] == WORKER_RUNNING:
                elapsed = time.monotonic() - job["started_at"]
                if elapsed > self._timeout_s:
                    out["status"] = WORKER_FAILED
                    out["reason"] = "timeout"
                    out["elapsed_s"] = round(elapsed, 1)
            return out

    def _run(self, job_id: str, jpeg: bytes, mask_png: bytes | None) -> None:
        started = time.monotonic()
        with self._lock:
            if self._jobs[job_id]["status"] != WORKER_QUEUED:
                return
            self._jobs[job_id]["status"] = WORKER_RUNNING
        prompt = self._jobs[job_id].get("prompt")
        try:
            mesh, timings = self._pipeline.generate(jpeg, mask_png)
            total_ms = (time.monotonic() - started) * 1000.0
            if total_ms > self._timeout_s * 1000.0:
                raise RuntimeError("timeout")

            # Publish atomically: readiness is the file existing, so a partial
            # write would be read as a finished GLB by the coordinator.
            # file_type is explicit because the staging name ends in .part, which
            # trimesh cannot map to an exporter on its own.
            self.artifact_root.mkdir(parents=True, exist_ok=True)
            final = self.artifact_root / f"{job_id}.glb"
            staging = self.artifact_root / f".{job_id}.glb.part"
            mesh.export(staging, file_type="glb")
            if not staging.is_file() or staging.stat().st_size == 0:
                raise RuntimeError("empty_export")
            os.replace(staging, final)

            timings["total_ms"] = round(total_ms, 1)
            with self._lock:
                self._jobs[job_id]["status"] = WORKER_READY
                self._jobs[job_id]["timings"] = timings
                # Release the GPU slot in the SAME critical section that publishes
                # the terminal status. Otherwise a client can observe "ready" and
                # still be refused with 409 on its next submit.
                if self._active_job == job_id:
                    self._active_job = None
            logger.info(
                "job %s ready prompt=%r sam_ms=%s tripo_ms=%s total_ms=%s verts=%s",
                job_id, prompt, timings.get("sam_ms"), timings.get("tripo_ms"),
                timings.get("total_ms"), timings.get("verts"),
            )
        except MaskRejected as exc:
            self._fail_locked(job_id, exc.reason)
            logger.warning(
                "job %s failed reason=%s coverage=%.4f prompt=%r",
                job_id, exc.reason, exc.coverage, prompt,
            )
        except Exception as exc:  # noqa: BLE001 - every failure must settle the job
            self._fail_locked(job_id, type(exc).__name__)
            logger.exception("job %s failed prompt=%r", job_id, prompt)
        finally:
            try:
                (self.artifact_root / f".{job_id}.glb.part").unlink(missing_ok=True)
            except OSError:
                pass
            # Safety net: a job that never reached a terminal publish still frees
            # the slot. Releasing twice is harmless.
            with self._lock:
                if self._active_job == job_id:
                    self._active_job = None

    def _fail_locked(self, job_id: str, reason: str) -> None:
        with self._lock:
            # Slot first, then the terminal status, all under one lock: a settled
            # job must never be observable while the worker still says it is busy.
            if self._active_job == job_id:
                self._active_job = None
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = WORKER_FAILED
                job["reason"] = reason


class _GenHandler(BaseHTTPRequestHandler):
    worker: GenerationWorker

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
        if self.path.rstrip("/") != "/jobs":
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
        status, payload = self.worker.submit(body)
        self._write_json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        if not self.path.startswith("/jobs/"):
            self._write_json(404, {"error": "not_found"})
            return
        job_id = self.path[len("/jobs/"):].split("?", 1)[0].strip("/")
        if not job_id:
            self._write_json(404, {"error": "not_found"})
            return
        payload = self.worker.status(job_id)
        if payload is None:
            self._write_json(404, {"error": "unknown_job", "job_id": job_id})
            return
        self._write_json(200, payload)


def start_generation_server(host: str, port: int, worker: GenerationWorker) -> ThreadingHTTPServer:
    handler = type("BoundGenHandler", (_GenHandler,), {"worker": worker})
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="gen-http")
    thread.start()
    return server


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    artifact_root = Path(os.environ.get("GEN_ARTIFACT_ROOT") or default_artifact_root())
    checkpoint = Path(os.environ.get(
        "SAM2_CHECKPOINT",
        "/home/nate/Projects/omni-3d-gen/sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
    ))
    config = os.environ.get("SAM2_CONFIG", "configs/sam2.1/sam2.1_hiera_t.yaml")
    host = os.environ.get("GEN_HOST", "127.0.0.1")
    port = int(os.environ.get("GEN_PORT", str(GEN_PORT)))

    if not checkpoint.is_file():
        logger.error("SAM2 checkpoint missing: %s", checkpoint)
        return 2

    pipeline = Pipeline(sam2_checkpoint=checkpoint, config=config)
    # Load eagerly and loudly: a worker that accepts jobs it can never run is
    # worse than one that refuses to start.
    try:
        pipeline.load()
    except Exception:
        logger.exception("model load failed; refusing to serve")
        return 3

    artifact_root.mkdir(parents=True, exist_ok=True)
    worker = GenerationWorker(artifact_root, pipeline)
    server = start_generation_server(host, port, worker)
    logger.info("generation worker on %s:%d -> %s", host, port, artifact_root)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
