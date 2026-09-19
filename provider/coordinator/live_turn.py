"""Live-session turn loop: stream mic in, stream speech out, interrupt honestly.

One persistent LiveSession per connection (warmed at hello). Mic chunks
forward live; the validated JPEG goes out as one image turn on
utterance_end; model audio returns as ordered speak_chunks plus a final
speak_final. Interruption tombstones the turn: late audio drops.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from coordinator.session import CoordinatorState
from voice.live_session import LiveSession, LiveSessionError
from voice.perception_image import validate_jpeg
from yibu_audit import append_audit_record

logger = logging.getLogger(__name__)

WINDOW_24K = 48000  # 1 s of 24 kHz mono s16le per speak_chunk
NO_IMAGE_RECOVERY = ("I couldn't get a camera image. "
                     "Check camera access or lighting, then ask again.")
SESSION_DOWN_RECOVERY = "Sorry, I couldn't reach the model. Try again."
IMAGE_TURN_PROMPT = ("Answer what I just asked about this image "
                     "in at most twenty-five words.")


class _Turn:
    def __init__(self, turn_id: int, utterance_id: str, send: Any) -> None:
        self.turn_id = turn_id
        self.utterance_id = utterance_id
        self.send = send
        self.event = asyncio.Event()
        self.audio24k = bytearray()
        self.said: list[str] = []
        self.seq = 0
        self.tombstoned = False
        self.pending: set = set()
        self.baseline: dict[str, int] = {}
        self.audio_out = bytearray()
        self.started = time.monotonic()
        self.last_usage: dict[str, int] = {}
        self.usage_delta: dict[str, int] = {}


def _callbacks(state: CoordinatorState):
    return dict(
        on_audio=lambda data: _on_audio(state, data),
        on_output_transcript=lambda t: _on_said(state, t),
        on_input_transcript=lambda t: None,
        on_interrupted=lambda: _on_interrupted(state),
        on_usage=lambda u: _on_usage(state, u),
        on_turn_end=lambda: _on_turn_end(state),
    )


async def ensure_live_session(state: CoordinatorState) -> bool:
    live = getattr(state, "live", None)
    if live is not None and live.is_open:
        return True
    factory = getattr(state, "live_factory", None)
    try:
        session = factory(**_callbacks(state)) if factory else LiveSession(**_callbacks(state))
        await session.connect()
    except LiveSessionError as exc:
        logger.info("live session unavailable exception_class=%s", type(exc).__name__)
        return False
    except Exception as exc:
        logger.info("live session unavailable exception_class=%s", type(exc).__name__)
        return False
    state.live = session
    return True


async def forward_audio(state: CoordinatorState, pcm: bytes) -> None:
    live = getattr(state, "live", None)
    if live is None or not live.is_open or not pcm:
        return
    try:
        await live.send_audio(bytes(pcm))
    except Exception as exc:
        logger.info("live audio forward failed exception_class=%s", type(exc).__name__)


async def speak_recovery(state: CoordinatorState, send: Any, turn_id: int,
                         utterance_id: str, text: str = SESSION_DOWN_RECOVERY) -> None:
    await send("speak", turn_id,
               {"turn_id": turn_id, "text": text, "audio": None}, utterance_id)


async def speak_down(state: CoordinatorState, send: Any, utterance_id: str) -> int:
    """Session unavailable: still open a turn so Quest never stalls 45 s."""
    state.turn_id += 1
    turn_id = state.turn_id
    await send("turn_started", turn_id,
               {"utterance_id": utterance_id, "turn_id": turn_id}, utterance_id)
    await speak_recovery(state, send, turn_id, utterance_id)
    return turn_id


async def start_live_turn(state: CoordinatorState, send: Any,
                          utterance_id: str, buf: Any) -> int | None:
    """Run one utterance through the live session. Returns turn_id or None."""
    from voice.bootstrap_diagnostics import turn_started as log_turn_started
    if getattr(state, "_live_turn", None) is not None:
        logger.info("VoiceBootstrap component=coordinator event=utterance_dropped reason=turn_busy")
        return None
    state.turn_id += 1
    turn_id = state.turn_id
    log_turn_started(turn_id=turn_id, mode="live_session", pcm_bytes=len(buf.pcm))
    await send("turn_started", turn_id,
               {"utterance_id": utterance_id, "turn_id": turn_id}, utterance_id)
    turn = _Turn(turn_id, utterance_id, send)
    turn.baseline = dict(getattr(state, "_live_last_totals", {}))
    state._live_turn = turn
    try:
        reason = validate_jpeg(buf.jpeg, buf.envelope)
        if reason:
            from voice.bootstrap_diagnostics import perception_frame
            perception_frame("perception_degraded", reason=reason)
            await speak_recovery(state, send, turn_id, utterance_id, NO_IMAGE_RECOVERY)
            return turn_id
        await state.live.start_image_turn(bytes(buf.jpeg), IMAGE_TURN_PROMPT)
        await turn.event.wait()
    except LiveSessionError as exc:
        logger.info("live turn failed exception_class=%s", type(exc).__name__)
        await speak_recovery(state, send, turn_id, utterance_id)
    except Exception as exc:
        logger.info("live turn failed exception_class=%s", type(exc).__name__)
        await speak_recovery(state, send, turn_id, utterance_id)
    finally:
        if getattr(state, "_live_turn", None) is turn:
            state._live_turn = None
    return turn_id


def _current(state: CoordinatorState) -> _Turn | None:
    return getattr(state, "_live_turn", None)


def _on_audio(state: CoordinatorState, data: bytes) -> None:
    turn = _current(state)
    if turn is None or turn.tombstoned or not data:
        return
    turn.audio24k.extend(data)
    while len(turn.audio24k) >= WINDOW_24K:
        window = bytes(turn.audio24k[:WINDOW_24K])
        del turn.audio24k[:WINDOW_24K]
        task = asyncio.create_task(_emit_chunk(turn, window))
        turn.pending.add(task)
        task.add_done_callback(turn.pending.discard)


async def _emit_chunk(turn: _Turn, window24k: bytes) -> None:
    if turn.tombstoned:
        return
    try:
        from voice.resample import resample_24k_to_16k
        pcm16k = await asyncio.to_thread(resample_24k_to_16k, window24k)
    except Exception as exc:
        logger.info("live chunk resample failed exception_class=%s", type(exc).__name__)
        return
    if turn.tombstoned:
        return
    turn.audio_out.extend(pcm16k)
    payload = {"turn_id": turn.turn_id, "seq": turn.seq,
               "audio": {"encoding": "pcm_s16le", "sample_rate": 16000,
                         "channels": 1,
                         "data_b64": base64.b64encode(pcm16k).decode("ascii")}}
    turn.seq += 1
    try:
        await turn.send("speak_chunk", turn.turn_id, payload, turn.utterance_id)
    except Exception:
        pass


def _on_said(state: CoordinatorState, text: str) -> None:
    turn = _current(state)
    if turn is not None and text:
        turn.said.append(text)


def _on_interrupted(state: CoordinatorState) -> None:
    turn = _current(state)
    if turn is None:
        return
    turn.tombstoned = True
    asyncio.create_task(_stop_turn(turn))


async def _stop_turn(turn: _Turn) -> None:
    try:
        await turn.send("stop_speak", turn.turn_id,
                        {"turn_id": turn.turn_id}, turn.utterance_id)
    except Exception:
        pass


def _on_usage(state: CoordinatorState, usage: dict) -> None:
    turn = _current(state)
    if turn is None or not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if isinstance(value, int) and not isinstance(value, bool):
            turn.last_usage[key] = value
            delta = value - turn.baseline.get(key, 0)
            if delta > 0:
                turn.usage_delta[key] = delta


def _on_turn_end(state: CoordinatorState) -> None:
    turn = _current(state)
    if turn is None:
        return
    asyncio.create_task(_finish_turn(state, turn))


async def _finish_turn(state: CoordinatorState, turn: _Turn) -> None:
    if turn.audio24k and not turn.tombstoned:
        rest = bytes(turn.audio24k)
        turn.audio24k.clear()
        task = asyncio.create_task(_emit_chunk(turn, rest))
        turn.pending.add(task)
        task.add_done_callback(turn.pending.discard)
    if turn.pending:
        await asyncio.gather(*list(turn.pending), return_exceptions=True)
    if turn.audio_out and not turn.tombstoned:
        import time as _time
        state.last_speak_pcm = bytes(turn.audio_out)
        state.last_speak_at = _time.monotonic()
    if not turn.tombstoned:
        text = "".join(turn.said).strip()
        gate = "passed" if turn.seq > 0 else "failed"
        from voice.bootstrap_diagnostics import synth_result
        synth_result(turn_id=turn.turn_id, voice_gate=gate, audio_bytes=turn.seq * 32000)
        try:
            await turn.send("speak_final", turn.turn_id,
                            {"turn_id": turn.turn_id, "text": text, "voice_gate": gate},
                            turn.utterance_id)
        except Exception:
            pass
    try:
        from voice.live_session import ENDPOINT, MODEL
    except ImportError:
        MODEL, ENDPOINT = "live", ""
    try:
        append_audit_record(model=MODEL, api_key="", endpoint=ENDPOINT,
                            purpose="rt-voice-turn", transport="websocket", ok=True,
                            status_code=101, latency_s=time.monotonic() - turn.started,
                            response_json={"usage_delta": dict(turn.usage_delta)})
    except Exception as exc:
        logger.info("live audit failed exception_class=%s", type(exc).__name__)
    state._live_last_totals = dict(turn.last_usage)
    turn.event.set()
