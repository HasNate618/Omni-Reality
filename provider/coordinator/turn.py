"""Voice turn loop (spec §7): utterance → plan → ops → ACK → speak.

`start_turn` runs inside the connection's receive loop, so it only does the
synchronous part (assign turn_id, send `turn_started`) and hands the rest to
a background task. The receive loop must stay free to take the very ACKs
that task is waiting on.

Speech never claims a drawing that Quest has not ACKed `placed`/`applied`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any, Awaitable, Callable

from jsonschema import ValidationError

from coordinator.planner import MAX_OPS_PER_TURN, PlanResult
from coordinator.session import CoordinatorState, UtteranceBuffer
from omni.tools import accept_model_ops
from protocol.ids import new_ulid
from protocol.validate import validate_instance
from voice.audio import BYTES_PER_SECOND, MIN_UTTERANCE_S

logger = logging.getLogger(__name__)

ACK_TIMEOUT_S = 1.5
CONTEXT_TURNS = 8

SAY_UNCONFIRMED = "I couldn't confirm placement."
SAY_MODEL_ERROR = "Sorry, I couldn't reach the model. Try again."
SAY_NO_TARGET = "I couldn't work out where to put that. Point at it or look closer."
SAY_PLACED_DEFAULT = "There it is."

# PlacementAck reason → spoken honesty copy (spec §9).
REJECT_COPY = {
    "out_of_camera": "That's outside the camera.",
    "too_small": "I need you closer.",
    "too_close": "Too close for depth.",
    "no_surface": "I can't plant that on a surface.",
}
STALE_COPY = "That moved, look again."
REJECT_DEFAULT = "I couldn't place that."

Send = Callable[..., Awaitable[None]]


def planner_binds_tools(planner: Any) -> bool:
    """Whether this planner is handed tool context for a turn.

    Single source of truth for a two-sided contract: `server.py` advertises the
    result as `accepts_frame` in hello_ok, and `_run_turn` gates the tool bind on
    it. If those two ever disagree, Quest either withholds the camera frame from a
    planner that needs one, or sends one to a planner that ignores it -- and on the
    withholding side every frame-anchored tool call is rejected with
    {"error": "invalid"} while the whole suite still passes, because the tests
    author the frame themselves.
    """
    if planner is None:
        return False
    if getattr(planner, "voice_only", False):
        return False
    if getattr(planner, "perception_qa", False):
        return False
    return getattr(planner, "bind_tools", None) is not None


def _turn_task_done(state: CoordinatorState, turn_id: int, task: asyncio.Task) -> None:
    """Pop turn registry; log unhandled failures (class + turn id only)."""
    state.turn_tasks.pop(turn_id, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.info(
            "turn background task failed turn_id=%d exception_class=%s",
            turn_id,
            type(exc).__name__,
        )


def start_turn(state: CoordinatorState, send: Send, utterance_id: str) -> asyncio.Task | None:
    """Close an utterance. Returns the background turn task, or None."""
    if utterance_id in state.closed_utterances:
        return None
    buf = state.utterances.pop(utterance_id, None) or UtteranceBuffer()
    state.closed_utterances.add(utterance_id)
    if getattr(state.planner, "perception_qa", False) and state.turn_tasks:
        logger.info("VoiceBootstrap component=coordinator event=utterance_dropped reason=turn_busy")
        return None
    if len(buf.pcm) < MIN_UTTERANCE_S * BYTES_PER_SECOND:
        logger.info("utterance %s too short (%d bytes); no turn", utterance_id, len(buf.pcm))
        return None
    state.turn_id += 1
    turn_id = state.turn_id
    task = asyncio.create_task(_run_turn(state, send, turn_id, utterance_id, buf))
    state.turn_tasks[turn_id] = task
    task.add_done_callback(lambda t: _turn_task_done(state, turn_id, t))
    return task


def cancel_turn(state: CoordinatorState, turn_id: int) -> None:
    """Quest barge-in: tombstone the turn so nothing more is sent for it."""
    state.cancelled_turns.add(turn_id)
    task = state.turn_tasks.get(turn_id)
    if task is not None:
        task.cancel()
    for op_id, op in list(state.pending_ops.items()):
        if op.get("turn_id") == turn_id:
            state.cancel_op(op_id)


def _to_scene_op(model_op: dict, turn_id: int, stage_epoch: int) -> dict | None:
    op = {"op_id": new_ulid(), "turn_id": turn_id, "stage_epoch": stage_epoch, "drawing_id": None}
    op.update(model_op)
    try:
        validate_instance("scene_op", op)
    except ValidationError as exc:
        # Warning, not info: by this point model ops have already passed
        # `model_scene_op`, so a drop here is usually a coordinator-authored op
        # that the tool loop already reported to the model as placed. Silently
        # dropping it means the wearer sees nothing where the model said there
        # would be something.
        logger.warning(
            "dropping op that fails scene_op schema: kind=%s reason=%s",
            model_op.get("kind"),
            exc.message,
        )
        return None
    return op


def spoken_line(plan: PlanResult, sent: list[dict], acks: dict[str, dict | None]) -> str:
    """Pick what to say given the plan and the ACKs that came back."""
    if not sent:
        if plan.proposed_op_count:
            return SAY_NO_TARGET
        return plan.text or SAY_NO_TARGET
    results = [acks.get(op["op_id"]) for op in sent]
    if any(ack is None for ack in results):
        return SAY_UNCONFIRMED
    for ack in results:
        if ack["status"] == "stale":
            return STALE_COPY
        if ack["status"] == "rejected":
            return REJECT_COPY.get(ack.get("reason"), REJECT_DEFAULT)
    return plan.text or SAY_PLACED_DEFAULT


async def _run_turn(
    state: CoordinatorState,
    send: Send,
    turn_id: int,
    utterance_id: str,
    buf: UtteranceBuffer,
) -> None:
    from voice.bootstrap_diagnostics import (
        planner_complete,
        planner_failed,
        planner_mode_label,
        synth_failed,
        synth_result,
        turn_started as log_turn_started,
    )

    pcm_len = len(buf.pcm)
    log_turn_started(
        turn_id=turn_id,
        mode=planner_mode_label(state.planner),
        pcm_bytes=pcm_len,
    )
    await send("turn_started", turn_id, {"utterance_id": utterance_id, "turn_id": turn_id}, utterance_id)
    bind = getattr(state.planner, "bind_tools", None)
    if bind is not None and planner_binds_tools(state.planner):
        try:
            bind(
                jobs=state.jobs,
                jpeg_b64=base64.b64encode(buf.jpeg).decode() if buf.jpeg else None,
                frame_id=(buf.envelope or {}).get("frame_id"),
                listings=state.listings,
                prebaked=state.prebaked,
            )
        except Exception:
            logger.exception("tool bind failed for turn %d", turn_id)
    try:
        plan = await state.planner.plan(
            pcm=bytes(buf.pcm),
            jpeg=buf.jpeg,
            envelope=buf.envelope,
            context=state.context[-CONTEXT_TURNS:],
        )
    except asyncio.CancelledError:
        raise
    except SystemExit:
        planner_failed(turn_id=turn_id, exception_class="SystemExit")
        if turn_id not in state.cancelled_turns:
            await send("speak", turn_id, {"turn_id": turn_id, "text": SAY_MODEL_ERROR, "audio": None}, utterance_id)
        return
    except Exception as exc:
        planner_failed(turn_id=turn_id, exception_class=type(exc).__name__)
        if turn_id not in state.cancelled_turns:
            await send("speak", turn_id, {"turn_id": turn_id, "text": SAY_MODEL_ERROR, "audio": None}, utterance_id)
        return
    if turn_id in state.cancelled_turns:
        return

    planner_complete(
        turn_id=turn_id,
        latency_ms=plan.latency_ms,
        ops_count=len(plan.ops),
    )

    stage_epoch = buf.envelope["stage_epoch"] if buf.envelope else state.latest_stage_epoch
    sent: list[dict] = []
    for model_op in plan.ops[:MAX_OPS_PER_TURN]:
        op = _to_scene_op(model_op, turn_id, stage_epoch)
        if op is not None:
            sent.append(op)
    # ops_closed: this list is final for the turn before anything is sent.
    state.ops_closed[turn_id] = [op["op_id"] for op in sent]
    events: dict[str, asyncio.Event] = {}
    for op in sent:
        state.pending_ops[op["op_id"]] = op
        events[op["op_id"]] = state.ack_events[op["op_id"]] = asyncio.Event()
    for op in sent:
        await send("scene_op", turn_id, op, utterance_id)

    if sent:
        try:
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in events.values())), ACK_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.info("turn %d: ACK timeout", turn_id)
        for op_id in events:
            # A late ACK still settles the op in state; it never speaks again.
            state.ack_events.pop(op_id, None)
    if turn_id in state.cancelled_turns:
        return

    acks = {op["op_id"]: state.completed_ops.get(op["op_id"]) for op in sent}
    line = spoken_line(plan, sent, acks)
    # Cloud speech after the ACK barrier (voice spec §2 step 7). The final
    # line is tools-disabled: no tool calls happen past this point.
    audio_block, voice_gate = await _speak_audio(state, line, turn_id)
    audio_bytes = 0
    if isinstance(audio_block, dict):
        try:
            pcm_out = base64.b64decode(audio_block.get("data_b64") or "", validate=True)
            audio_bytes = len(pcm_out)
        except (binascii.Error, ValueError):
            pcm_out = b""
        if pcm_out:
            import time as _time
            state.last_speak_pcm = bytes(pcm_out)
            state.last_speak_at = _time.monotonic()
    synth_result(turn_id=turn_id, voice_gate=voice_gate, audio_bytes=audio_bytes)
    await send(
        "speak", turn_id,
        {"turn_id": turn_id, "text": line, "audio": audio_block},
        utterance_id,
    )
    state.context.append(
        {
            "turn_id": turn_id,
            "heard": plan.heard,
            "said": line,
            "drawing_ids": [a["drawing_id"] for a in acks.values() if a and a.get("drawing_id")],
            "latency_ms": plan.latency_ms,
            "audit_id": plan.audit_id,
            "voice_gate": voice_gate,
        }
    )
    del state.context[:-CONTEXT_TURNS]


def ingest_audio_chunk(state: CoordinatorState, utterance_id: str | None, payload: dict[str, Any]) -> None:
    """Append one audio_chunk's PCM to its utterance buffer."""
    utterance_id = payload.get("utterance_id") or utterance_id
    audio = payload.get("audio")
    if not isinstance(utterance_id, str) or not isinstance(audio, dict):
        logger.info("ignoring audio_chunk without utterance_id/audio")
        return
    if (audio.get("encoding"), audio.get("sample_rate"), audio.get("channels")) != ("pcm_s16le", 16000, 1):
        logger.info("ignoring audio_chunk with unsupported format")
        return
    try:
        pcm = base64.b64decode(audio.get("data_b64") or "", validate=True)
    except (binascii.Error, ValueError):
        logger.info("ignoring audio_chunk with bad base64")
        return
    if not state.accepts_utterance(utterance_id) or len(pcm) % 2:
        return
    buf = state.utterances.setdefault(utterance_id, UtteranceBuffer())
    if len(buf.pcm) + len(pcm) > state.max_utterance_bytes:
        logger.info("utterance %s over cap; dropping chunk", utterance_id)
        return
    buf.pcm.extend(pcm)


async def _speak_audio(
    state: CoordinatorState, line: str, turn_id: int
) -> tuple[dict | None, str]:
    """Synthesize the final line. Returns (audio_block_or_None, voice_gate)."""
    import inspect as inspect_module

    from voice.cloud_speech import audio_block

    synth = getattr(state, "synthesizer", None)
    if synth is None:
        return None, "degraded"
    try:
        pcm = synth(line)
        if inspect_module.iscoroutine(pcm):
            pcm = await pcm
    except Exception as exc:
        from voice.bootstrap_diagnostics import synth_failed

        synth_failed(turn_id=turn_id, exception_class=type(exc).__name__)
        return None, "failed"
    if not pcm:
        return None, "failed"
    return audio_block(bytes(pcm)), "passed"


PlaceSend = Callable[[dict], Awaitable[dict | None]]
FinalFn = Callable[[dict], Awaitable[None]]


def may_speak(acks: list[dict] | None, timed_out: bool) -> bool:
    if timed_out:
        return False
    if acks is None:
        return False
    if not acks:
        return True
    for ack in acks:
        status = ack.get("status")
        if status not in ("placed", "applied"):
            return False
    return True


async def freeze_and_ack(
    *,
    send_ops: Callable[[dict], Awaitable[None]],
    wait_acks: Callable[[list[dict], float], Awaitable[list[dict] | None]],
    ops: list[dict],
    timeout_s: float = ACK_TIMEOUT_S,
) -> list[dict]:
    frozen = list(ops)
    for op in frozen:
        await send_ops(op)
    return await wait_acks(frozen, timeout_s) or []


def build_place_generated(
    *,
    job_id: str,
    turn_id: int,
    stage_epoch: int,
    target: dict,
    extent_m: list[float] | None = None,
    name: str | None = None,
) -> dict:
    op: dict = {
        "op_id": new_ulid(),
        "turn_id": turn_id,
        "stage_epoch": stage_epoch,
        "kind": "place_generated",
        "drawing_id": None,
        "job_id": job_id,
        "target": target,
    }
    if extent_m is not None:
        op["extent_m"] = [float(axis) for axis in extent_m]
    if name:
        # The same optional field the immediate listing op carries, so the
        # job-completion op labels the chips too instead of degrading to dims.
        op["name"] = name
    validate_instance("scene_op", op)
    return op


async def on_job_terminal(
    state: CoordinatorState,
    job_id: str,
    send: PlaceSend,
    complete_final_fn: FinalFn,
) -> None:
    job = state.jobs.jobs.get(job_id)
    if job is None:
        return
    status = job.get("status")
    if status not in ("ready", "failed"):
        return
    stage_epoch = int(job.get("stage_epoch") or state.latest_stage_epoch)
    if stage_epoch < state.latest_stage_epoch:
        return
    if status == "failed":
        await complete_final_fn({"status": "failed"})
        return
    if not job.get("extent_m"):
        # Spec §6.3: a size claim needs a size, and Quest refuses an extentless
        # `place_generated` rather than inventing one. This branch is live now
        # that the job poller settles queued jobs, and the shop-to-life jobs
        # (handle_start_generation) carry no extents at all, so emitting here
        # would buy a refusal nobody waits on and nobody hears. Report the
        # failure and emit nothing.
        await complete_final_fn({"status": "failed"})
        return
    if job.get("planted"):
        # Planted when the item was accepted, so a stated-size box has been in
        # the room since before the mesh existed. Emitting again would place
        # the same object twice. Quest is already fetching the artifact.
        await complete_final_fn({"status": "ready", "planted": True})
        return
    turn_id = max(state.turn_id, 1)
    target = job.get("target")
    if not isinstance(target, dict):
        await complete_final_fn({"status": "failed"})
        return
    # An unplanted job that does state its size still places at that size:
    # same rule, an op Quest would only refuse is not worth sending, and this
    # one has the metres to plant with. (`handle_place_item` plants at accept,
    # so this is the record carrying extents without the planted flag, not the
    # live listing path.)
    op = build_place_generated(
        job_id=job_id,
        turn_id=turn_id,
        stage_epoch=stage_epoch,
        target=target,
        extent_m=job["extent_m"],
        name=job.get("name"),
    )
    ack = await send(op)
    if ack is None:
        await complete_final_fn({"status": "failed"})
        return
    await complete_final_fn(ack)
