"""Session-scoped inspect objects and generation jobs (Task 5)."""

from __future__ import annotations

import inspect as inspect_module
from pathlib import Path
from typing import Any, Awaitable, Callable

from protocol.ids import new_ulid
from workers.gen_client import BusyError

InspectFn = Callable[..., Any]
QueueFn = Callable[..., Any]
TerminalFn = Callable[[str], Awaitable[None]]
ExistsFn = Callable[[Path], bool]
StatusFn = Callable[..., Any]

# The worker's own job vocabulary (docs/superpowers/specs/2026-09-19-omni-worker-tools-design.md):
# queued | running | ready | failed. Only the terminal failure string is acted on.
WORKER_FAILED = "failed"

# Consecutive scans a queued job may report an unreachable worker before it is
# settled failed. More than one, so a restart or a slow status reply is not read
# as death; small enough that the session does not stay wedged.
MAX_UNREACHABLE_SCANS = 10


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
    frame_id: str | None,
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
    frame_id: str | None,
    jpeg_b64: str,
    target: dict[str, Any],
    phrase: str | None,
    current_frame_id: str | None,
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


def _object_target(frame_id: str | None, obj: dict[str, Any]) -> dict[str, Any]:
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
    current_frame_id: str | None,
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


def _is_file(path: Path) -> bool:
    return path.is_file()


async def poll_queued_jobs(
    store: JobStore,
    artifact_root: Path,
    *,
    on_terminal: TerminalFn,
    exists_fn: ExistsFn = _is_file,
    status_fn: StatusFn | None = None,
) -> list[str]:
    """Settle every queued job whose artifact landed, or whose worker failed.

    One scan, no loop: the caller owns cadence and cancellation. The artifact
    file is the readiness signal because it is exactly what the artifact server
    checks before serving a job; the worker's status is consulted only to learn
    about a terminal failure, so a worker that dies cannot leave a job queued
    forever and block every later placement (spec §5.3 session_generation_busy).

    Returns the job ids settled in this scan. A job whose worker is briefly
    unreachable stays queued, but once it has been unreachable for
    MAX_UNREACHABLE_SCANS consecutive scans it is settled failed: "cannot reach
    the worker" and "never coming" are indistinguishable to a wearer staring at
    nothing, and a job parked in `queued` refuses every later placement.
    """
    # Local import: artifacts.py imports this module for JobStore/
    # coordinator_may_place, so a module-level import would be a cycle.
    from coordinator.artifacts import artifact_path

    settled: list[str] = []
    for job_id, job in list(store.jobs.items()):
        if job.get("status") != "queued":
            continue
        path = artifact_path(artifact_root, job_id)
        if path is not None and exists_fn(path):
            mark_ready(store, job_id)
        elif status_fn is None:
            continue
        else:
            status = await _call_injected(status_fn, job_id=job_id)
            if status == WORKER_FAILED:
                mark_failed(store, job_id)
            elif status is None:
                # None is "could not tell": the worker is down or unreadable.
                unreachable = int(job.get("unreachable_scans") or 0) + 1
                job["unreachable_scans"] = unreachable
                if unreachable < MAX_UNREACHABLE_SCANS:
                    continue
                mark_failed(store, job_id)
            else:
                # A reachable worker reporting queued/running/ready: reset the
                # tolerance so a merely slow job is never settled early.
                job["unreachable_scans"] = 0
                continue
        settled.append(job_id)
        await on_terminal(job_id)
    return settled


def clear_jobs(store: JobStore, artifact_root: Path | None = None) -> None:
    job_records = list(store.jobs.items())
    store.objects.clear()
    store.jobs.clear()
    if artifact_root is None:
        return
    for job_id, job in job_records:
        # A pre-baked artifact belongs to the operator, not the session: it is
        # the night-before bake, and unlinking it would make take two a 404
        # (spec §5.3). Session-owned artifacts are still reclaimed.
        if job.get("prebaked"):
            continue
        path = artifact_root / f"{job_id}.glb"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
