"""Session-scoped inspect objects and generation jobs (Task 5)."""

from __future__ import annotations

import inspect as inspect_module
from pathlib import Path
from typing import Any, Callable

from protocol.ids import new_ulid
from workers.gen_client import BusyError

InspectFn = Callable[..., Any]
QueueFn = Callable[..., Any]


class JobStore:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}


async def _call_injected(fn: InspectFn | QueueFn, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect_module.iscoroutine(result):
        return await result
    return result


def _inspect_result(
    frame_id: str,
    *,
    count: int = 0,
    objects: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "frame_id": frame_id,
        "count": count,
        "objects": objects or [],
    }
    if error is not None:
        out["error"] = error
    return out


async def handle_inspect(
    store: JobStore,
    *,
    frame_id: str,
    jpeg_b64: str,
    target: dict[str, Any],
    phrase: str | None,
    current_frame_id: str,
    inspect_fn: InspectFn,
) -> dict[str, Any]:
    if frame_id != current_frame_id:
        return _inspect_result(frame_id, error="invalid")

    try:
        worker = await _call_injected(
            inspect_fn,
            frame_id=frame_id,
            jpeg_b64=jpeg_b64,
            target=target,
            phrase=phrase,
        )
    except TimeoutError:
        return _inspect_result(frame_id, error="timeout")

    raw_objects = worker.get("objects") if isinstance(worker, dict) else None
    if not raw_objects:
        return _inspect_result(frame_id, error="no_object")

    ranked = sorted(raw_objects, key=lambda o: float(o.get("score", 0)), reverse=True)[:3]
    public: list[dict[str, Any]] = []
    for item in ranked:
        object_id = new_ulid()
        mask = item.get("mask_png_b64")
        stored: dict[str, Any] = {
            "frame_id": frame_id,
            "u0": item["u0"],
            "v0": item["v0"],
            "u1": item["u1"],
            "v1": item["v1"],
            "score": item["score"],
        }
        if mask is not None:
            stored["mask_png_b64"] = mask
        store.objects[object_id] = stored
        public.append({
            "object_id": object_id,
            "u0": item["u0"],
            "v0": item["v0"],
            "u1": item["u1"],
            "v1": item["v1"],
            "score": item["score"],
        })

    return _inspect_result(frame_id, count=len(public), objects=public)


def _object_target(frame_id: str, obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "image_box",
        "frame_id": frame_id,
        "u0": obj["u0"],
        "v0": obj["v0"],
        "u1": obj["u1"],
        "v1": obj["v1"],
    }


def session_generation_busy(store: JobStore) -> bool:
    """True while any worker job is queued or running (one job at a time)."""
    for job in store.jobs.values():
        if job.get("status") in ("queued", "running"):
            return True
    return False


async def handle_start_generation(
    store: JobStore,
    *,
    args: dict[str, Any],
    current_frame_id: str,
    jpeg_b64: str | None,
    queue_fn: QueueFn,
) -> dict[str, Any]:
    if not jpeg_b64:
        return {"error": "missing_image"}

    if session_generation_busy(store):
        return {"error": "busy"}

    object_id = args.get("object_id")
    if not object_id or object_id not in store.objects:
        return {"error": "unknown_object"}

    obj = store.objects[str(object_id)]
    if obj.get("frame_id") != current_frame_id:
        return {"error": "invalid"}

    target = _object_target(current_frame_id, obj)
    job_id = new_ulid()
    prompt = args.get("prompt")
    mask = obj.get("mask_png_b64")

    store.jobs[job_id] = {
        "job_id": job_id,
        "frame_id": current_frame_id,
        "target": target,
        "object_id": str(object_id),
        "status": "queued",
    }

    try:
        await _call_injected(
            queue_fn,
            job_id=job_id,
            frame_id=current_frame_id,
            jpeg_b64=jpeg_b64,
            prompt=prompt,
            mask_png_b64=mask,
        )
    except BusyError:
        store.jobs.pop(job_id, None)
        return {"error": "busy"}
    except BaseException:
        # Never strand a job: a queued job left behind makes
        # session_generation_busy true for the rest of the session, which
        # refuses every later generation and placement.
        store.jobs.pop(job_id, None)
        raise

    return {"job_id": job_id, "status": "queued", "frame_id": current_frame_id}


def coordinator_may_place(store: JobStore, job_id: str) -> bool:
    job = store.jobs.get(job_id)
    return job is not None and job.get("status") == "ready"


def mark_ready(store: JobStore, job_id: str) -> None:
    job = store.jobs.get(job_id)
    if job is not None:
        job["status"] = "ready"


def mark_failed(store: JobStore, job_id: str) -> None:
    job = store.jobs.get(job_id)
    if job is not None:
        job["status"] = "failed"


def clear_jobs(store: JobStore, artifact_root: Path | None = None) -> None:
    job_ids = list(store.jobs.keys())
    store.objects.clear()
    store.jobs.clear()
    if artifact_root is None:
        return
    for job_id in job_ids:
        path = artifact_root / f"{job_id}.glb"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
