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
LIVE_TURN_TIMEOUT_S = 12.0
NO_IMAGE_RECOVERY = ("I couldn't get a camera image. "
                     "Check camera access or lighting, then ask again.")
SESSION_DOWN_RECOVERY = "Sorry, I couldn't reach the model. Try again."
HIGHLIGHT_DECL = {
    "name": "highlight_object",
    "description": ("Highlight one visible object for the wearer. "
                      "u/v locate it as fractions across the attached photo."),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {"type": "string",
                       "description": "Short name of the object, e.g. the red mug."},
            "u": {"type": "number",
                  "description": "Horizontal position, 0 left to 1 right."},
            "v": {"type": "number",
                  "description": "Vertical position, 0 top to 1 bottom."},
        },
        "required": ["label", "u", "v"],
    },
}

IMAGE_TURN_PROMPT = (
    "You are the wearer's assistant standing here with them. "
    "Answer what I just asked directly in one or two short sentences. "
    "Never ask me what I want to know, and never mention images, "
    "photos, or length limits. Describe only what is visible; "
    "never invent details. If I asked you to track, highlight, follow, "
    "or find something visible, the app is already outlining it for me "
    "on its own, so just confirm briefly and naturally. Never say you "
    "are unable to track or follow something."
)

# B-mode can start an overlay mid-conversation. The Live session returns
# speech, not coordinates, and the gateway never sends inputTranscription --
# we ask for it, but only outputTranscription ever arrives -- so there is no
# transcript of the wearer to trigger on. Instead each conversation turn runs
# A-mode's tracking planner in the background over the same audio and frame:
# it answers track:null unless an object was actually asked for, which is a
# better arbiter than phrase matching. OMNI_LIVE_TRACK=0 turns it off and
# spends one model call per turn instead of two.
TRACK_PHRASES = (
    "track the", "track that", "track my", "track this",
    "highlight the", "highlight that", "highlight this",
    "outline the", "outline that",
    "follow the", "follow that",
    "what's that", "what is that", "whats that",
)


def live_tracking_enabled() -> bool:
    """False when OMNI_LIVE_TRACK is set to 0/false/no."""
    import os

    return os.environ.get("OMNI_LIVE_TRACK", "1").strip().lower() not in ("0", "false", "no")


def wants_tracking(text: str) -> bool:
    """True when the wearer asked for an object to be tracked."""
    low = (text or "").lower()
    return any(phrase in low for phrase in TRACK_PHRASES)


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
        # Media for this utterance, kept so a tracking seed can reuse exactly
        # what the model was asked about (see _maybe_seed_tracking).
        self.jpeg: bytes | None = None
        self.envelope: dict | None = None
        self.pcm: bytes = b""
        self.heard: list[str] = []
        self.seed_started = False


def _callbacks(state: CoordinatorState) -> dict[str, Any]:
    return dict(
        on_audio=lambda data: _on_audio(state, data),
        on_output_transcript=lambda t: _on_said(state, t),
        on_input_transcript=lambda t: _on_heard(state, t),
        on_interrupted=lambda: _on_interrupted(state),
        on_usage=lambda u: _on_usage(state, u),
        on_turn_end=lambda: _on_turn_end(state),
        on_tool_call=lambda name, args, call_id: _on_tool_call(state, name, args, call_id),
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


async def speak_recovery(state: CoordinatorState, send: Any, turn_id: int,
                         utterance_id: str, text: str = SESSION_DOWN_RECOVERY) -> None:
    await send("speak", turn_id,
               {"turn_id": turn_id, "text": text, "audio": None}, utterance_id)


async def _recycle_session(state: CoordinatorState) -> None:
    """Drop a silent session so the next hello/utterance warms a fresh one."""
    live = getattr(state, "live", None)
    state.live = None
    if live is not None:
        try:
            await live.close()
        except Exception:
            pass


async def speak_down(state: CoordinatorState, send: Any, utterance_id: str) -> int:
    """Session unavailable: still open a turn so Quest never stalls 45 s."""
    state.turn_id += 1
    turn_id = state.turn_id
    await send("turn_started", turn_id,
               {"utterance_id": utterance_id, "turn_id": turn_id}, utterance_id)
    await speak_recovery(state, send, turn_id, utterance_id)
    return turn_id


def parse_highlight_args(args: Any, sent_w: Any = None, sent_h: Any = None) -> dict | None:
    """Validate highlight_object args into a seed target with label.

    u/v are fractions across the question photo; bare pixels are accepted
    too (the model habit) and normalized when the photo size is known.
    Anything else returns None: the tool answers unseeded, never invents."""
    import math
    if not isinstance(args, dict):
        return None
    label = args.get("label")
    u, v = args.get("u"), args.get("v")
    if not isinstance(label, str) or not label.strip():
        return None
    if any(isinstance(n, bool) or not isinstance(n, (int, float))
           or not math.isfinite(n) for n in (u, v)):
        return None
    if u > 1:
        if not isinstance(sent_w, (int, float)) or sent_w <= 0:
            return None
        u = u / sent_w
    if v > 1:
        if not isinstance(sent_h, (int, float)) or sent_h <= 0:
            return None
        v = v / sent_h
    if not 0 <= u <= 1 or not 0 <= v <= 1:
        return None
    return {"type": "image_point", "u": u, "v": v, "label": label.strip()[:64]}


class _Preempted(Exception):
    """The turn was tombstoned (barge-in) mid-seed: stop, never paint."""


def _on_tool_call(state: CoordinatorState, name: str, args: dict, call_id: str) -> None:
    """Dispatch a Live function call (sync callback: schedule the work)."""
    if name != HIGHLIGHT_DECL["name"] or not call_id:
        return
    logger.info("VoiceBootstrap component=coordinator event=highlight_tool_called")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(handle_highlight_tool(state, args, call_id))


async def handle_highlight_tool(state: CoordinatorState, args: dict, call_id: str) -> None:
    """Seed SAM2 tracking from highlight_object and answer the tool call.

    Seeds on the in-flight turn's question frame; the tool response tells
    the model whether the highlight is live. No bridge, frame, or target:
    honest failure, never a guess."""
    from coordinator.sam2_bridge import TrackingError
    ok, text = False, "Highlighting is unavailable."
    live = getattr(state, "live", None)
    turn = getattr(state, "_live_turn", None)
    bridge = getattr(state, "tracking", None)
    framed = getattr(state, "_live_frame", None)
    if (live is not None and turn is not None and not turn.tombstoned
            and bridge is not None and framed is not None):
        envelope, jpeg = framed
        target = parse_highlight_args(args, envelope.get("sent_w"), envelope.get("sent_h"))
        if target is not None and jpeg:
            try:
                generation = await bridge.begin(turn.turn_id, turn.utterance_id)
                if turn.tombstoned:
                    raise _Preempted()
                frame = bridge.history.add(envelope, jpeg)
                await bridge.seed(frame, target, generation)
                if turn.tombstoned:
                    raise _Preempted()
                ok, text = True, f"Selecting {target['label']}…"
            except _Preempted:
                try:
                    await bridge.stop()
                except Exception:
                    pass
                text = "Interrupted."
            except TrackingError as exc:
                text = str(exc)
            except Exception as exc:
                logger.info("highlight seed failed exception_class=%s", type(exc).__name__)
                text = "Highlighting is unavailable."
    if live is not None:
        try:
            await live.send_tool_response(call_id, HIGHLIGHT_DECL["name"],
                                          {"ok": ok, "message": text})
        except Exception as exc:
            logger.info("highlight tool response dropped exception_class=%s",
                        type(exc).__name__)


async def start_live_turn(state: CoordinatorState, send: Any,
                          utterance_id: str, buf: Any) -> int | None:
    """Run one utterance through the live session. Returns turn_id or None."""
    from voice.bootstrap_diagnostics import turn_started as log_turn_started
    previous = getattr(state, "_live_turn", None)
    if previous is not None:
        # Voice preemption: the wearer spoke over the reply. Stop the old
        # audio on Quest, tombstone the turn (late audio drops, no final).
        previous.tombstoned = True
        previous.event.set()
        try:
            await send("stop_speak", previous.turn_id,
                       {"turn_id": previous.turn_id}, previous.utterance_id)
        except Exception:
            pass
        logger.info("VoiceBootstrap component=coordinator event=turn_preempted "
                    "turn_id=%d", previous.turn_id)
    state.turn_id += 1
    turn_id = state.turn_id
    state._live_frame = None
    if getattr(state, "tracking", None) is not None:
        # A new question supersedes any live track (including barge-in).
        try:
            await state.tracking.stop()
        except Exception:
            pass
    log_turn_started(turn_id=turn_id, mode="live_session", pcm_bytes=len(buf.pcm))
    await send("turn_started", turn_id,
               {"utterance_id": utterance_id, "turn_id": turn_id}, utterance_id)
    turn = _Turn(turn_id, utterance_id, send)
    turn.baseline = dict(getattr(state, "_live_last_totals", {}))
    turn.jpeg = bytes(buf.jpeg) if buf.jpeg else None
    turn.envelope = buf.envelope
    turn.pcm = bytes(buf.pcm)
    state._live_turn = turn
    try:
        reason = validate_jpeg(buf.jpeg, buf.envelope)
        if reason:
            from voice.bootstrap_diagnostics import perception_frame
            perception_frame("perception_degraded", reason=reason)
            await speak_recovery(state, send, turn_id, utterance_id, NO_IMAGE_RECOVERY)
            return turn_id
        state._live_frame = (buf.envelope, bytes(buf.jpeg))
        live_session = state.live
        if live_session is None:
            raise LiveSessionError("live session missing")
        await live_session.start_image_turn(bytes(buf.jpeg), bytes(buf.pcm), IMAGE_TURN_PROMPT)
        _maybe_seed_tracking(state, turn)
        try:
            await asyncio.wait_for(turn.event.wait(), LIVE_TURN_TIMEOUT_S)
        except asyncio.TimeoutError:
            live = getattr(state, "live", None)
            logger.info(
                "VoiceBootstrap component=coordinator event=live_turn_timeout "
                "turn_id=%d sent_frames=%d received_events=%d",
                turn_id,
                getattr(live, "sent_frames", -1),
                getattr(live, "received_events", -1))
            await _recycle_session(state)
            await speak_recovery(state, send, turn_id, utterance_id)
            return turn_id
    except LiveSessionError as exc:
        logger.info("live turn failed exception_class=%s", type(exc).__name__)
        await speak_recovery(state, send, turn_id, utterance_id)
    except Exception as exc:
        logger.info("live turn failed exception_class=%s", type(exc).__name__)
        await speak_recovery(state, send, turn_id, utterance_id)
    finally:
        if getattr(state, "_live_turn", None) is turn:
            state._live_turn = None
        state._live_frame = None
    return turn_id


def _current(state: CoordinatorState) -> _Turn | None:
    # Late events from a preempted turn may land here while the new turn
    # runs (the session tags nothing). Rare and mild: a few extra chunks
    # at most, since turns serialize seconds apart and process in order.
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


def _maybe_seed_tracking(state: CoordinatorState, turn: _Turn) -> None:
    """Start one background tracking selection for this turn, at most once."""
    if turn.seed_started or state.tracking is None or not turn.jpeg:
        return
    if not live_tracking_enabled():
        return
    turn.seed_started = True
    # Background: the conversation must never block on the seed.
    task = asyncio.create_task(_seed_tracking(state, turn))
    task.add_done_callback(_log_seed_done)


def _on_heard(state: CoordinatorState, text: str) -> None:
    """Wearer transcript, when the gateway sends one. Today it does not, so
    the per-turn attempt in start_live_turn is what actually seeds."""
    turn = _current(state)
    if turn is None or not text:
        return
    turn.heard.append(text)
    if wants_tracking("".join(turn.heard)):
        _maybe_seed_tracking(state, turn)


def _log_seed_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.info("live tracking seed failed exception_class=%s", type(exc).__name__)


async def _seed_tracking(state: CoordinatorState, turn: _Turn) -> None:
    """Pick a point from this utterance's frame and seed SAM 2.

    Reuses A-mode's tracking planner and Sam2Bridge untouched. Stays silent on
    failure: B-mode should never talk over a live reply.
    """
    bridge = state.tracking
    if bridge is None or not turn.jpeg or not turn.envelope:
        return
    from coordinator.planner import YibuPlanner

    generation = await bridge.begin(turn.turn_id, turn.utterance_id)
    planner = YibuPlanner(tracking=True, purpose="track-object")
    plan = await planner.plan(pcm=turn.pcm, jpeg=turn.jpeg,
                              envelope=turn.envelope, context=[])
    if turn.tombstoned or generation != bridge.generation:
        return
    if not plan.tracking_targets:
        logger.info("live tracking seed: no target for turn %d", turn.turn_id)
        return
    frame = await bridge.history.wait_for(turn.envelope.get("frame_id"))
    await bridge.seed(frame, plan.tracking_targets, generation)
    logger.info("live tracking seed: turn %d seeded %d objects from frame %s",
                turn.turn_id, len(plan.tracking_targets), turn.envelope.get("frame_id"))


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
