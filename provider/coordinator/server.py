"""Offline LAN coordinator server (Task 6).

Handles WebSocket `hello`/`hello_ok`, `ping`/`pong`, and emits exactly one
laptop-authored hardcoded `scene_op` mark after the first valid `frame`.
`hello` alone never emits a mark: the production Quest sequence is hello
first and a real frame later, and only the frame carries a resolvable
frame_id/stage_epoch for the mark target.

Offline by construction: this module never imports yibuapi, never reads
any API key, never invokes a model, and never produces speech. Outbound
frames are always v1 JSON text. Inbound frames are validated defensively;
invalid input is logged and ignored without crashing the connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from jsonschema import ValidationError

from coordinator.session import CoordinatorState
from protocol.ids import new_ulid
from protocol.validate import validate_instance

logger = logging.getLogger(__name__)

CLOCK_SKEW_THRESHOLD_NS = 2_000_000_000

_MARK_STYLE = {"color": "#3DDCFF", "label": None}
_MARK_MOTION = {"kind": "pulse", "period_s": 1.2}


def _laptop_now_ns() -> int:
    return time.time_ns()


def _sendable(
    msg_type: str,
    session_id: str | None,
    turn_id: int,
    payload: dict,
) -> str:
    """Build a validated v1 JSON text wrapper frame."""
    message = {
        "v": 1,
        "type": msg_type,
        "session_id": session_id,
        "turn_id": turn_id,
        "utterance_id": None,
        "payload": payload,
    }
    validate_instance("message", message)
    return json.dumps(message)


async def _send_mark(
    ws: Any,
    state: CoordinatorState,
    *,
    stage_epoch: int,
    frame_id: str,
) -> None:
    """Emit the single hardcoded mark and record it as pending."""
    op = {
        "op_id": new_ulid(),
        "turn_id": 1,
        "stage_epoch": stage_epoch,
        "kind": "mark",
        "drawing_id": None,
        "target": {"type": "capture_hint", "frame_id": frame_id},
        "style": dict(_MARK_STYLE),
        "motion": dict(_MARK_MOTION),
    }
    validate_instance("scene_op", op)
    await ws.send(
        _sendable("scene_op", state.session_id, 1, op),
    )
    state.pending_ops[op["op_id"]] = op
    state.turn_id = 1
    state.mark_sent = True


def _check_clock_skew(state: CoordinatorState, quest_t_ns: object) -> None:
    """Log (and record) skew when Quest/laptop clocks differ by > 2 s.

    Poses are never rewritten; the envelope is left untouched.
    """
    if not isinstance(quest_t_ns, int) or isinstance(quest_t_ns, bool):
        return
    if quest_t_ns < 0:
        return
    skew_ns = quest_t_ns - _laptop_now_ns()
    if abs(skew_ns) > CLOCK_SKEW_THRESHOLD_NS:
        state.last_clock_skew_ns = skew_ns
        logger.warning(
            "clock skew exceeded threshold: clock_skew_ns=%d quest_t_unix_ns=%d",
            skew_ns,
            quest_t_ns,
        )


def _extract_envelope(payload: object) -> dict | None:
    """Return the capture envelope from a frame payload, or None.

    Accepts both {"envelope": {...}} and a bare envelope object.
    """
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("envelope", payload)
    if not isinstance(candidate, dict):
        return None
    try:
        validate_instance("capture_envelope", candidate)
    except ValidationError as exc:
        logger.info("ignoring frame with invalid envelope: %s", exc.message)
        return None
    return candidate


async def _handle_hello(ws: Any, state: CoordinatorState, message: dict) -> None:
    incoming = message["session_id"]
    if incoming is not None and incoming == state.session_id:
        session_id = state.session_id
    else:
        session_id = new_ulid()
        state.session_id = session_id
    await ws.send(
        _sendable(
            "hello_ok",
            session_id,
            0,
            {"session_id": session_id, "laptop_t_unix_ns": _laptop_now_ns()},
        )
    )
    # NOTE: no mark here by design. The production Quest sequence is hello
    # first, real frame later; emitting a mark on hello would carry a random
    # frame_id no client can resolve and would consume the single mark
    # (mark_sent) before the resolvable frame arrives. First valid frame
    # emits the one mark; see _handle_frame.


async def _handle_ping(ws: Any, state: CoordinatorState, message: dict) -> None:
    await ws.send(
        _sendable(
            "pong",
            state.session_id,
            0,
            {"laptop_t_unix_ns": _laptop_now_ns()},
        )
    )


async def _handle_frame(ws: Any, state: CoordinatorState, message: dict) -> None:
    envelope = _extract_envelope(message["payload"])
    if envelope is None:
        return
    if not state.is_epoch_fresh(envelope["stage_epoch"]):
        logger.info(
            "discarding stale envelope: stage_epoch=%d latest=%d",
            envelope["stage_epoch"],
            state.latest_stage_epoch,
        )
        return
    state.accept_envelope(envelope)
    _check_clock_skew(state, envelope.get("t_unix_ns"))
    if not state.mark_sent:
        await _send_mark(
            ws,
            state,
            stage_epoch=envelope["stage_epoch"],
            frame_id=envelope["frame_id"],
        )


async def _handle_ack(ws: Any, state: CoordinatorState, message: dict) -> None:
    """Record a Quest placement ack (Task 7); settled ops never retry."""
    payload = message["payload"]
    if not isinstance(payload, dict):
        logger.info("ignoring ack with non-object payload")
        return
    try:
        validate_instance("placement_ack", payload)
    except ValidationError as exc:
        logger.info("ignoring schema-invalid ack: %s", exc.message)
        return
    op_id = payload["op_id"]
    if state.is_op_settled(op_id):
        logger.debug("ignoring duplicate ack for settled op: %s", op_id)
        return
    if op_id not in state.pending_ops:
        logger.info("ignoring ack for unknown op: %s", op_id)
        return
    state.complete_op(op_id, payload)
    logger.info("ack op=%s status=%s", op_id, payload["status"])


async def _handle_cancel(ws: Any, state: CoordinatorState, message: dict) -> None:
    """Record a Quest cancel for a pending op (Task 7 priority path)."""
    payload = message["payload"]
    if not isinstance(payload, dict):
        logger.info("ignoring cancel with non-object payload")
        return
    op_id = payload.get("op_id")
    if not isinstance(op_id, str):
        logger.info("ignoring cancel without op_id")
        return
    if state.is_op_settled(op_id):
        logger.debug("ignoring duplicate cancel for settled op: %s", op_id)
        return
    if op_id not in state.pending_ops:
        logger.info("ignoring cancel for unknown op: %s", op_id)
        return
    state.cancel_op(op_id)
    logger.info("cancel op=%s", op_id)


async def handle_text(ws: Any, state: CoordinatorState, raw: object) -> None:
    """Parse, validate, and dispatch one inbound frame; never raises."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw_text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            logger.info("ignoring non-utf8 binary frame")
            return
    elif isinstance(raw, str):
        raw_text = raw
    else:
        logger.info("ignoring non-text frame of type %s", type(raw).__name__)
        return
    try:
        message = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        logger.info("ignoring non-JSON frame")
        return
    try:
        validate_instance("message", message)
    except ValidationError as exc:
        logger.info("ignoring schema-invalid message: %s", exc.message)
        return
    if not state.is_session_allowed(message["session_id"]):
        logger.info("ignoring message from foreign session")
        return
    msg_type = message["type"]
    if msg_type == "hello":
        await _handle_hello(ws, state, message)
    elif msg_type == "ping":
        await _handle_ping(ws, state, message)
    elif msg_type == "frame":
        await _handle_frame(ws, state, message)
    elif msg_type == "ack":
        await _handle_ack(ws, state, message)
    elif msg_type == "cancel":
        await _handle_cancel(ws, state, message)
    else:
        logger.debug("ignoring unhandled message type: %s", msg_type)


async def handle_connection(ws: Any, state: CoordinatorState) -> None:
    """Serve one connection until it closes or the task is cancelled."""
    if hasattr(ws, "recv"):
        while True:
            try:
                raw = await ws.recv()
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            await handle_text(ws, state, raw)
    else:  # async-iterable socket (e.g. websockets server connection)
        try:
            async for raw in ws:
                await handle_text(ws, state, raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            return


async def run_server(host: str = "0.0.0.0", port: int = 8765) -> None:
    """Bind the coordinator WebSocket server (CLI: python -m coordinator.server)."""
    import websockets

    async def _serve_one(ws) -> None:
        await handle_connection(ws, CoordinatorState())

    async with websockets.serve(_serve_one, host, port):
        logger.info("coordinator listening on %s:%d", host, port)
        await asyncio.Future()  # serve forever


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_server())
