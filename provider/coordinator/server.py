"""Offline LAN coordinator server (Task 6).

Handles WebSocket `hello`/`hello_ok`, `ping`/`pong`, and emits exactly one
laptop-authored hardcoded `scene_op` mark after the first valid `frame`.

With a planner on the state (`--planner stub|yibu`), frames and
`audio_chunk`s feed utterances instead, and `utterance_end` runs a voice
turn (coordinator/turn.py). Only the yibu planner calls a model.
`hello` alone never emits a mark: the production Quest sequence is hello
first and a real frame later, and only the frame carries a resolvable
frame_id/stage_epoch for the mark target.

--sam2-url opts into voice-seeded tracking: frame JPEGs feed a bounded history,
utterance_end names the selected frame, and tracking results return on the
same Quest socket. The receive loop stays active while selection/tracking run.

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
from voice.audio import BYTES_PER_SECOND, MIN_UTTERANCE_S
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
    if _live_mode(state):
        asyncio.create_task(_warm_live_session(state))
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
    if state.tracking is not None:
        bridge = state.tracking
        if bridge.epoch != envelope["stage_epoch"]:
            if bridge.epoch is not None:
                for turn_id in list(state.turn_tasks):
                    cancel_turn(state, turn_id)
                await bridge.reset()
                await bridge.status("stopped", "Tracking origin changed; select the object again.")
            bridge.epoch = envelope["stage_epoch"]
        from coordinator.sam2_bridge import TrackingError
        try:
            encoded = message["payload"].get("jpeg_b64")
            if not isinstance(encoded, str) or len(encoded) > 470_000:
                raise TrackingError("Missing or oversized camera JPEG.")
            jpeg = base64.b64decode(encoded, validate=True)
            bridge.history.add(envelope, jpeg)
            logger.debug(
                "video frame accepted: frame_id=%s jpeg=%d bytes %dx%d epoch=%d",
                envelope["frame_id"], len(jpeg), envelope["sent_w"], envelope["sent_h"],
                envelope["stage_epoch"],
            )
        except (TrackingError, binascii.Error, ValueError) as exc:
            logger.warning("Tracking frame rejected: %s", exc)
        # A frame tagged with an utterance is the snapshot for a B-mode turn,
        # not just tracker input: the live turn sends buf.jpeg to the model.
        # Without this the tracker swallowed it and every conversation turn
        # reported missing_image. Streamed A-mode frames carry no utterance_id
        # and stop at the tracker, as before.
        if state.planner is not None and (
                message["payload"].get("utterance_id") or message["utterance_id"]):
            _attach_frame_to_utterance(state, message, envelope)
        return
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
    logger.debug(
        "frame pinned to utterance %s: frame_id=%s jpeg=%s bytes",
        utterance_id, envelope["frame_id"], len(jpeg) if jpeg else 0,
    )


def _turn_sender(ws: Any, state: CoordinatorState):
    async def send(msg_type: str, turn_id: int, payload: dict, utterance_id: str | None = None) -> None:
        logger.debug("-> %s turn=%s", msg_type, turn_id)
        try:
            await ws.send(_sendable(msg_type, state.session_id, turn_id, payload, utterance_id))
        except Exception:  # socket gone: drawings stay on Quest, nothing to retry
            logger.info("send %s failed; connection closed?", msg_type)

    return send


async def _warm_speech(purpose: str) -> None:
    """Pre-synthesize the canned lines; failures are not worth a turn."""
    from coordinator.turn import SAY_MODEL_ERROR, SAY_NO_TARGET, SAY_TRACKING_DEFAULT
    from voice.cloud_speech import warm_line

    for line in (SAY_TRACKING_DEFAULT, SAY_NO_TARGET, SAY_MODEL_ERROR):
        try:
            await warm_line(line, purpose=purpose)
        except Exception as exc:
            logger.info("speech warm failed exception_class=%s", type(exc).__name__)
            return
    logger.info("canned speech warmed (%d lines)", 3)


async def _warm_live_session(state: CoordinatorState) -> None:
    from coordinator import live_turn as live_mod
    await live_mod.ensure_live_session(state)


def _live_mode(state: CoordinatorState) -> bool:
    from coordinator.planner import VoiceStubPlanner
    return bool(getattr(state.planner, "perception_qa", False)) and not isinstance(
        state.planner, VoiceStubPlanner)


async def _handle_audio_chunk(ws: Any, state: CoordinatorState, message: dict) -> None:
    if state.planner is None:
        return
    # Buffered only: mic audio travels inside the explicit image turn.
    # (Streaming it live wedges the session behind an implicit VAD turn.)
    ingest_audio_chunk(state, message["utterance_id"], message["payload"])


async def _handle_utterance_end(ws: Any, state: CoordinatorState, message: dict) -> None:
    if state.planner is None:
        return
    utterance_id = message["payload"].get("utterance_id") or message["utterance_id"]
    if not isinstance(utterance_id, str):
        logger.info("ignoring utterance_end without utterance_id")
        return
    # A-mode (push-to-talk) and B-mode (live conversation) share one session,
    # so the route is chosen per utterance, not by a startup flag. Older
    # headset builds omit `mode`; they mean push-to-talk.
    mode = message["payload"].get("mode")
    if mode not in ("ptt", "live"):
        # No mode: an older headset build, or one started in a fixed mode by
        # --perception-qa. Otherwise push-to-talk.
        mode = "live" if _live_mode(state) else "ptt"

    from voice.bootstrap_diagnostics import utterance_end_accepted

    buf = state.utterances.get(utterance_id)
    pcm_bytes = len(buf.pcm) if buf is not None else 0
    utterance_end_accepted(pcm_bytes)
    logger.info(
        "utterance_end %s: mode=%s audio=%d bytes (%.2f s), selected frame_id=%s",
        utterance_id, mode, pcm_bytes, pcm_bytes / 32000,
        message["payload"].get("frame_id"),
    )
    if buf is not None and pcm_bytes:
        from voice.echo_gate import peak_rms, voiced_windows
        import struct as _struct
        _pcm = bytes(buf.pcm)
        _n = len(_pcm) // 2
        _vals = _struct.unpack("<%dh" % _n, _pcm) if _n else ()
        _zero = sum(1 for _v in _vals if _v == 0)
        _zc = sum(1 for _a, _b in zip(_vals, _vals[1:])
                  if (_a < 0) != (_b < 0) and _a != 0 and _b != 0)
        logger.info("VoiceBootstrap component=coordinator event=utterance_levels "
                    "voiced=%d peak_rms=%.4f zero_frac=%.3f zcr=%.4f n=%d",
                    voiced_windows(_pcm), peak_rms(_pcm),
                    _zero / _n if _n else 1.0, _zc / _n if _n else 0.0, _n)
    if buf is not None and len(buf.pcm) < MIN_UTTERANCE_S * BYTES_PER_SECOND:
        logger.info("utterance %s too short (%d bytes); no turn", utterance_id, len(buf.pcm))
        return
    if buf is not None and _drop_phantom(state, _turn_sender(ws, state), utterance_id, buf):
        return
    if mode == "live":
        _start_live_utterance(state, _turn_sender(ws, state), utterance_id)
        return
    if state.tracking is not None:
        frame_id = message["payload"].get("frame_id")
        state.utterances.setdefault(utterance_id, UtteranceBuffer()).selected_frame_id = (
            frame_id if isinstance(frame_id, str) else None
        )
    start_turn(state, _turn_sender(ws, state), utterance_id)


def _start_live_utterance(state: CoordinatorState, send: Any, utterance_id: str) -> None:
    """Session path: pop the buffer and run the live turn in background."""
    from coordinator import live_turn as live_mod
    from coordinator.turn import _turn_task_done
    if utterance_id in state.closed_utterances:
        return
    buf = state.utterances.pop(utterance_id, None) or UtteranceBuffer()
    state.closed_utterances.add(utterance_id)
    if len(buf.pcm) < MIN_UTTERANCE_S * BYTES_PER_SECOND:
        logger.info("utterance %s too short (%d bytes); no turn", utterance_id, len(buf.pcm))
        return
    # B-mode is reached by the per-utterance `mode`, not a startup flag, so the
    # session may not be warmed yet. Connect on first use; only report it down
    # if that fails.
    task = asyncio.create_task(_live_turn_or_down(state, send, utterance_id, buf))
    task.add_done_callback(_log_live_task_done)


async def _live_turn_or_down(state: CoordinatorState, send: Any,
                             utterance_id: str, buf: Any) -> None:
    from coordinator import live_turn as live_mod
    live = getattr(state, "live", None)
    if live is None or not live.is_open:
        if not await live_mod.ensure_live_session(state):
            await _live_down(state, send, utterance_id)
            return
    await live_mod.start_live_turn(state, send, utterance_id, buf)


async def _live_down(state: CoordinatorState, send: Any, utterance_id: str) -> None:
    from coordinator import live_turn as live_mod
    await live_mod.speak_down(state, send, utterance_id)


def _log_live_task_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.info("live turn background task failed exception_class=%s", type(exc).__name__)


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
        if state.tracking is not None and state.tracking.turn_id == turn_id:
            await state.tracking.stop()
            await state.tracking.status("stopped", "Tracking stopped.")
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


def _drop_phantom(state: CoordinatorState, send: Any, utterance_id: str, buf: Any) -> bool:
    """Blips and speaker echo never become turns (no model call, no chatter)."""
    from voice.echo_gate import should_drop
    drop, reason, score = should_drop(
        utterance_pcm=bytes(buf.pcm), last_speak_pcm=state.last_speak_pcm,
        speak_sent_at=state.last_speak_at or None, utterance_end_at=time.monotonic())
    if not drop:
        return False
    logger.info("VoiceBootstrap component=coordinator event=utterance_dropped "
                "reason=%s echo_score=%.2f", reason, score)
    state.utterances.pop(utterance_id, None)
    state.closed_utterances.add(utterance_id)
    # stop_speak releases the Quest mic gate silently: no caption, no tone.
    asyncio.create_task(_silent_drop(state, send, utterance_id))
    return True


async def _silent_drop(state: CoordinatorState, send: Any, utterance_id: str) -> None:
    try:
        await send("stop_speak", state.turn_id, {"turn_id": state.turn_id}, utterance_id)
    except Exception:
        pass


async def _close_live(state: CoordinatorState) -> None:
    live = getattr(state, "live", None)
    state.live = None
    if live is not None:
        try:
            await live.close()
        except Exception:
            pass


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
    logger.debug(
        "<- %s %d bytes utt=%s payload=%s",
        msg_type, len(raw_text), message["utterance_id"],
        ",".join(sorted(message["payload"])) if isinstance(message["payload"], dict) else "?",
    )
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
    await _close_live(state)
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
        await _close_live(state)
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
    tracking: bool = False,
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
        if tracking:
            from coordinator.planner import PlanResult

            class TrackingStub(StubPlanner):
                async def plan(self, **kwargs):
                    return PlanResult(ops=[], text="", tracking_target={"type": "image_point", "u": 0.5, "v": 0.5})
            return TrackingStub()
        return StubPlanner()
    if kind == "voice-stub":
        return VoiceStubPlanner(perception_qa=perception_qa)
    if perception_qa:
        from coordinator.perception import PerceptionQaPlanner
        return PerceptionQaPlanner(**({"model": model} if model else {}))
    if tracking:
        options = {"tracking": True, "purpose": "track-object"}
        if model:
            options["model"] = model
        return YibuPlanner(**options)
    purpose = "voice-only-turn" if voice_only else "voice-turn"
    if model:
        return YibuPlanner(model=model, voice_only=voice_only, purpose=purpose)
    return YibuPlanner(voice_only=voice_only, purpose=purpose)


async def run_server(
    host: str = "0.0.0.0",
    port: int = 8765,
    planner_kind: str = "mark",
    model: str | None = None,
    sam2_url: str | None = None,
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
        state = CoordinatorState(planner=make_planner(
            planner_kind, model, tracking=bool(sam2_url),
            voice_only=voice_only, perception_qa=perception_qa))
        if sam2_url:
            from coordinator.sam2_bridge import Sam2Bridge
            state.tracking = Sam2Bridge(sam2_url, _turn_sender(ws, state))
        if planner_kind == "voice-stub":
            from voice.test_tone import make_test_tone

            async def _tone_synth(_text: str) -> bytes:
                return make_test_tone()

            state.synthesizer = _tone_synth
        elif planner_kind == "yibu":
            # Live cloud speech for speak.audio (spends credit per turn).
            # A-mode replies carry audio: null and are spoken by Android TTS.
            from voice.cloud_speech import synthesize_line

            speak_purpose = "perception-qa-speak" if perception_qa else ("voice-only-speak" if voice_only else "voice-speak")

            async def _live_synth(text: str) -> bytes | None:
                return await synthesize_line(text=text, purpose=speak_purpose)

            state.synthesizer = _live_synth
            # Warm the fixed lines so the first turn does not pay for them.
            asyncio.create_task(_warm_speech(speak_purpose))
        try:
            await handle_connection(ws, state)
        finally:
            tasks = list(state.turn_tasks.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if state.tracking:
                await state.tracking.stop()

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
    parser.add_argument("--sam2-url", help="enable single-object tracking, e.g. ws://127.0.0.1:8766")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"],
        help="DEBUG traces every message, frame and model call",
    )
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
    if args.sam2_url and args.planner == "mark":
        parser.error("--sam2-url requires --planner stub or --planner yibu")
    try:
        ensure_live_voice_only_config(args.planner, args.voice_only)
        if args.planner == "yibu" and args.perception_qa:
            ensure_env_api_key("YIBU_API_KEY")
    except ApiKeyConfigurationError as exc:
        parser.error(
            f"Live coordinator requires environment variable {exc.name} "
            "(set on the laptop only; no API call is made when it is missing)."
        )
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Third-party debug logs bury ours (httpx prints every HTTP chunk).
    for noisy in ("websockets", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(
        run_server(
            args.host,
            args.port,
            args.planner,
            args.model,
            sam2_url=args.sam2_url,
            voice_only=args.voice_only,
            perception_qa=args.perception_qa,
        )
    )
