"""Offline LAN coordinator server (Task 6).

Handles WebSocket `hello`/`hello_ok`, `ping`/`pong`, and emits exactly one
laptop-authored hardcoded `scene_op` mark after the first valid `frame`.

With a planner on the state (`--planner stub|yibu`), frames and
`audio_chunk`s feed utterances instead, and `utterance_end` runs a voice
turn (coordinator/turn.py). Only the yibu planner calls a model.
`hello` alone never emits a mark: the production Quest sequence is hello
first and a real frame later, and only the frame carries a resolvable
frame_id/stage_epoch for the mark target.

This module never imports yibuapi or reads an API key itself; the
default (no planner) path never invokes a model or produces speech. Outbound
frames are always v1 JSON text. Inbound frames are validated defensively;
invalid input is logged and ignored without crashing the connection.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import time
from typing import Any

from jsonschema import ValidationError

from coordinator.live_config import ensure_live_voice_only_config
from coordinator.session import CoordinatorState, UtteranceBuffer
from coordinator.turn import cancel_turn, ingest_audio_chunk, start_turn
from yibu_audit import ApiKeyConfigurationError, ensure_env_api_key
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
    utterance_id: str | None = None,
) -> str:
    """Build a validated v1 JSON text wrapper frame."""
    message = {
        "v": 1,
        "type": msg_type,
        "session_id": session_id,
        "turn_id": turn_id,
        "utterance_id": utterance_id,
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
    except ValidationError:
        logger.info("VoiceBootstrap component=coordinator event=jpeg_rejected reason=invalid_envelope")
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
            {
                "session_id": session_id,
                "laptop_t_unix_ns": _laptop_now_ns(),
                "artifact_port": state.artifact_port,
                "perception_qa": bool(getattr(state.planner, "perception_qa", False)),
            },
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
    if state.planner is not None:
        _attach_frame_to_utterance(state, message, envelope)
        return
    if not state.mark_sent:
        await _send_mark(
            ws,
            state,
            stage_epoch=envelope["stage_epoch"],
            frame_id=envelope["frame_id"],
        )


def _attach_frame_to_utterance(state: CoordinatorState, message: dict, envelope: dict) -> None:
    """Keep the latest envelope + JPEG for the frame's open utterance."""
    payload = message["payload"]
    utterance_id = payload.get("utterance_id") or message["utterance_id"]
    if not isinstance(utterance_id, str):
        logger.info("frame without utterance_id; envelope kept, no utterance")
        return
    if not state.accepts_utterance(utterance_id):
        return
    buf = state.utterances.setdefault(utterance_id, UtteranceBuffer())
    perception = getattr(state.planner, "perception_qa", False)
    if perception and buf.frame_received:
        return
    buf.frame_received = True
    jpeg = None
    encoded = payload.get("jpeg_b64")
    from voice.perception_image import MAX_JPEG_BASE64, validate_jpeg
    from voice.bootstrap_diagnostics import perception_frame

    if isinstance(encoded, str) and (not perception or len(encoded) <= MAX_JPEG_BASE64):
        try:
            jpeg = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            pass
    if perception:
        reason = "image_over_cap" if isinstance(encoded, str) and len(encoded) > MAX_JPEG_BASE64 else validate_jpeg(jpeg, envelope)
        if reason:
            perception_frame("jpeg_rejected", reason=reason)
            jpeg = None
        else:
            perception_frame("frame_accepted", jpeg_bytes=len(jpeg))
    buf.envelope = envelope
    buf.jpeg = jpeg


def _turn_sender(ws: Any, state: CoordinatorState):
    async def send(msg_type: str, turn_id: int, payload: dict, utterance_id: str | None = None) -> None:
        try:
            await ws.send(_sendable(msg_type, state.session_id, turn_id, payload, utterance_id))
        except Exception:  # socket gone: drawings stay on Quest, nothing to retry
            logger.info("send %s failed; connection closed?", msg_type)

    return send


async def _handle_audio_chunk(ws: Any, state: CoordinatorState, message: dict) -> None:
    if state.planner is None:
        return
    ingest_audio_chunk(state, message["utterance_id"], message["payload"])


async def _handle_utterance_end(ws: Any, state: CoordinatorState, message: dict) -> None:
    if state.planner is None:
        return
    utterance_id = message["payload"].get("utterance_id") or message["utterance_id"]
    if not isinstance(utterance_id, str):
        logger.info("ignoring utterance_end without utterance_id")
        return
    from voice.bootstrap_diagnostics import utterance_end_accepted

    buf = state.utterances.get(utterance_id)
    pcm_bytes = len(buf.pcm) if buf is not None else 0
    utterance_end_accepted(pcm_bytes)
    start_turn(state, _turn_sender(ws, state), utterance_id)


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
    turn_id = payload.get("turn_id")
    if isinstance(turn_id, int) and not isinstance(turn_id, bool) and "op_id" not in payload:
        cancel_turn(state, turn_id)
        logger.info("cancel turn=%d", turn_id)
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
    except ValidationError:
        logger.info("VoiceBootstrap component=coordinator event=message_rejected reason=invalid_schema")
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
    elif msg_type == "audio_chunk":
        await _handle_audio_chunk(ws, state, message)
    elif msg_type == "utterance_end":
        await _handle_utterance_end(ws, state, message)
    elif msg_type == "clear_session":
        await _handle_clear_session(ws, state, message)
    else:
        logger.debug("ignoring unhandled message type: %s", msg_type)


async def _handle_clear_session(ws: Any, state: CoordinatorState, message: dict) -> None:
    from coordinator.jobs import clear_jobs

    clear_jobs(state.jobs, state.artifact_root)
    await state.clear_voice()
    payload = {"session_id": state.session_id, "generation": state.clear_generation}
    state.clear_generation += 1
    await ws.send(_sendable("session_cleared", state.session_id, 0, payload))


async def handle_connection(ws: Any, state: CoordinatorState) -> None:
    """Serve one connection until it closes or the task is cancelled."""
    from coordinator.jobs import clear_jobs
    from voice.bootstrap_diagnostics import connection_close, connection_open

    connection_open()
    try:
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
    finally:
        connection_close()
        if state.planner is not None:
            await state.clear_voice()
        clear_jobs(state.jobs, state.artifact_root)


def _validate_cli_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if getattr(args, "voice_only", False) and args.planner != "yibu":
        parser.error("--voice-only requires --planner yibu")
    if getattr(args, "perception_qa", False):
        if getattr(args, "voice_only", False):
            parser.error("--perception-qa and --voice-only are mutually exclusive")
        if args.planner not in ("yibu", "voice-stub"):
            parser.error("--perception-qa requires --planner yibu or voice-stub")


def make_planner(
    kind: str,
    model: str | None = None,
    *,
    voice_only: bool = False,
    perception_qa: bool = False,
):
    """None (slice-2 hardcoded mark), offline planners, or live Yibu."""
    _validate_cli_args(argparse.ArgumentParser(), argparse.Namespace(
        planner=kind, voice_only=voice_only, perception_qa=perception_qa))
    if kind == "mark":
        return None
    from coordinator.planner import StubPlanner, VoiceStubPlanner, YibuPlanner

    if kind == "stub":
        return StubPlanner()
    if kind == "voice-stub":
        return VoiceStubPlanner(perception_qa=perception_qa)
    if perception_qa:
        from coordinator.perception import PerceptionQaPlanner
        return PerceptionQaPlanner(**({"model": model} if model else {}))
    purpose = "voice-only-turn" if voice_only else "voice-turn"
    if model:
        return YibuPlanner(model=model, voice_only=voice_only, purpose=purpose)
    return YibuPlanner(voice_only=voice_only, purpose=purpose)


async def run_server(
    host: str = "0.0.0.0",
    port: int = 8765,
    planner_kind: str = "mark",
    model: str | None = None,
    voice_only: bool = False,
    perception_qa: bool = False,
) -> None:
    """Bind the coordinator WebSocket server (CLI: python -m coordinator.server)."""
    _validate_cli_args(argparse.ArgumentParser(), argparse.Namespace(
        planner=planner_kind, voice_only=voice_only, perception_qa=perception_qa))
    ensure_live_voice_only_config(planner_kind, voice_only)
    if planner_kind == "yibu" and perception_qa:
        ensure_env_api_key("YIBU_API_KEY")
    import websockets

    async def _serve_one(ws) -> None:
        state = CoordinatorState(
            planner=make_planner(planner_kind, model, voice_only=voice_only, perception_qa=perception_qa)
        )
        if planner_kind == "voice-stub":
            from voice.test_tone import make_test_tone

            async def _tone_synth(_text: str) -> bytes:
                return make_test_tone()

            state.synthesizer = _tone_synth
        elif planner_kind == "yibu":
            # Live cloud speech for speak.audio (spends credit per turn).
            from voice.cloud_speech import synthesize_line

            speak_purpose = "perception-qa-speak" if perception_qa else ("voice-only-speak" if voice_only else "voice-speak")

            async def _live_synth(text: str) -> bytes | None:
                return await synthesize_line(text=text, purpose=speak_purpose)

            state.synthesizer = _live_synth
        await handle_connection(ws, state)

    async with websockets.serve(_serve_one, host, port):
        logger.info("coordinator listening on %s:%d (planner=%s)", host, port, planner_kind)
        await asyncio.Future()  # serve forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Omni-Reality LAN coordinator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--planner",
        choices=["mark", "stub", "yibu", "voice-stub"],
        default="mark",
        help=(
            "mark: slice-2 hardcoded mark (default); stub: offline voice turns; "
            "voice-stub: offline transport tone; yibu: live model (spends credit)"
        ),
    )
    parser.add_argument("--model", help="yibu model id (default qwen3.8-omni-flash)")
    parser.add_argument(
        "--voice-only",
        action="store_true",
        help="audio-only yibu turns (no image/tools); requires --planner yibu",
    )
    parser.add_argument(
        "--perception-qa", action="store_true",
        help="one camera image per spoken question, no tools; yibu or offline voice-stub",
    )
    args = parser.parse_args()
    _validate_cli_args(parser, args)
    try:
        ensure_live_voice_only_config(args.planner, args.voice_only)
        if args.planner == "yibu" and args.perception_qa:
            ensure_env_api_key("YIBU_API_KEY")
    except ApiKeyConfigurationError as exc:
        parser.error(
            f"Live coordinator requires environment variable {exc.name} "
            "(set on the laptop only; no API call is made when it is missing)."
        )
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        run_server(
            args.host,
            args.port,
            args.planner,
            args.model,
            voice_only=args.voice_only,
            perception_qa=args.perception_qa,
        )
    )
