"""Listing memory (spec §4): sizes the model stated, keyed by name.

Rows are recorded when a listing is placed, never on a guess. A row with no
stated sizes is not a row this store can hold, so every row is placeable and
the honesty gate lives in the caller's validation, not here.
"""

from __future__ import annotations

from typing import Any

from coordinator.jobs import JobStore, session_generation_busy
from coordinator.layout import pack_offsets, run_length
from protocol.ids import new_ulid

MIN_AXIS_M = 0.05
MAX_AXIS_M = 3.0
MAX_NAME_CHARS = 40
_FRAME_TARGETS = frozenset({"capture_hint", "pointing", "image_point", "image_box"})


def normalize_name(name: str) -> str:
    """Fold case and collapse whitespace so repeats update one row."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split()).strip().lower()


class ListingMemory:
    """Session-scoped listing rows keyed by normalized name."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def record(
        self,
        name: str,
        extent_m: list[float],
        source_frame_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        key = normalize_name(name)
        row = {
            "name": " ".join(name.split()) if isinstance(name, str) else "",
            "extent_m": [float(axis) for axis in extent_m],
            "source_frame_id": source_frame_id,
            "source": source,
        }
        self._rows[key] = row
        return row

    def get(self, name: str) -> dict[str, Any] | None:
        return self._rows.get(normalize_name(name))

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows.values())

    def clear(self) -> None:
        self._rows.clear()


def _valid_extents(value: Any) -> list[float] | None:
    """Three numbers, each inside the axis bounds. Anything else is None."""
    if not isinstance(value, list) or len(value) != 3:
        return None
    axes: list[float] = []
    for axis in value:
        # bool is an int subclass; a true/false axis is not a stated size.
        if isinstance(axis, bool) or not isinstance(axis, (int, float)):
            return None
        number = float(axis)
        if number < MIN_AXIS_M or number > MAX_AXIS_M:
            return None
        axes.append(number)
    return axes


def _valid_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > MAX_NAME_CHARS:
        return None
    return name


def _valid_target(value: Any, current_frame_id: str | None) -> dict | None:
    """Image-space target on the frame we just captured. Never a world point."""
    if not isinstance(value, dict):
        return None
    if value.get("type") not in _FRAME_TARGETS:
        return None
    if not current_frame_id or value.get("frame_id") != current_frame_id:
        return None
    return dict(value)


async def handle_place_item(
    store: JobStore,
    *,
    listings: "ListingMemory",
    args: dict[str, Any],
    current_frame_id: str | None,
    prebaked: dict[str, str],
    queue_fn: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate one place_item call. Returns (tool_result, coordinator_op).

    The caller owns sending the op: this runs inside a tool round with no turn
    id or stage epoch available. A refusal returns {"error": "invalid"} or
    {"error": "busy"} and leaves no trace: no row is recorded and no worker
    is queued, so later pack offsets and the run length are unaffected.
    """
    name = _valid_name(args.get("name"))
    extents = _valid_extents(args.get("extent_m"))
    target = _valid_target(args.get("target"), current_frame_id)
    if name is None or extents is None or target is None:
        return {"error": "invalid"}, None

    # Local import: prebaked.py imports normalize_name from this module.
    from coordinator.prebaked import lookup

    artifact_id = lookup(prebaked, name)
    if artifact_id is not None:
        # No worker is queued on this path, so the busy rule does not apply.
        store.jobs[artifact_id] = {
            "job_id": artifact_id,
            "frame_id": current_frame_id,
            "target": target,
            "object_id": None,
            "status": "ready",
            "extent_m": extents,
            "planted": True,
        }
        return _place_result(listings, name, extents, target, current_frame_id, artifact_id)

    from workers.gen_client import BusyError

    # The busy rule protects the one-worker queue, so it only applies when a
    # worker is actually bound. Nothing queued means nothing to serialize.
    if queue_fn is not None and session_generation_busy(store):
        return {"error": "busy"}, None

    job_id = new_ulid()
    store.jobs[job_id] = {
        "job_id": job_id,
        "frame_id": current_frame_id,
        "target": target,
        "object_id": None,
        "status": "queued",
        "extent_m": extents,
        "planted": True,
    }
    if queue_fn is not None:
        try:
            await _maybe_await(queue_fn(
                job_id=job_id,
                frame_id=current_frame_id,
                jpeg_b64=None,
                prompt=name,
                mask_png_b64=None,
                extent_m=extents,
            ))
        except BusyError:
            # The worker refused, so no placement happened. No row was recorded
            # yet, so there is nothing to roll back.
            store.jobs.pop(job_id, None)
            return {"error": "busy"}, None

    return _place_result(listings, name, extents, target, current_frame_id, job_id)


def _place_result(
    listings: "ListingMemory",
    name: str,
    extents: list[float],
    target: dict,
    current_frame_id: str | None,
    job_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Record the row and build (tool_result, coordinator_op) in one place.

    Both success paths go through here so `run_length_m` and the offset can
    never drift between them. Recording happens here, after every refusal
    point, which is what keeps a refused placement side-effect free.
    """
    listings.record(name, extents, current_frame_id, "page")
    return (
        {
            "listed": name,
            "extent_m": extents,
            "job_id": job_id,
            "run_length_m": run_length([row["extent_m"] for row in listings.rows()]),
        },
        _place_op(job_id, extents, target, _offset_for(listings, name)),
    )


def _offset_for(listings: "ListingMemory", name: str) -> float:
    """Frame-relative centre offset for this row, in recorded order (spec §7.1).

    Rows keep their insertion position when a repeat placement updates them, so
    an item does not jump sideways when the wearer restates its size.
    """
    rows = listings.rows()
    offsets = pack_offsets([row["extent_m"] for row in rows])
    if not offsets:
        return 0.0
    key = normalize_name(name)
    for index, row in enumerate(rows):
        if normalize_name(row["name"]) == key:
            return offsets[index]
    return 0.0


def _place_op(job_id: str, extents: list[float], target: dict, offset_m: float) -> dict[str, Any]:
    """Partial place_generated. _run_turn adds op_id, turn_id, stage_epoch."""
    return {
        "kind": "place_generated",
        "job_id": job_id,
        "extent_m": list(extents),
        "offset_m": float(offset_m),
        "target": dict(target),
        "drawing_id": None,
    }


async def _maybe_await(value: Any) -> Any:
    import inspect as inspect_module

    if inspect_module.iscoroutine(value):
        return await value
    return value
