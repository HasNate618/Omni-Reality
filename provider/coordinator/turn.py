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


def start_turn(state: CoordinatorState, send: Send, utterance_id: str) -> asyncio.Task | None:
    """Close an utterance. Returns the background turn task, or None."""
    buf = state.utterances.pop(utterance_id, None) or UtteranceBuffer()
    if len(buf.pcm) < MIN_UTTERANCE_S * BYTES_PER_SECOND:
        logger.info("utterance %s too short (%d bytes); no turn", utterance_id, len(buf.pcm))
        return None
    state.turn_id += 1
    turn_id = state.turn_id
    task = asyncio.create_task(_run_turn(state, send, turn_id, utterance_id, buf))
    state.turn_tasks[turn_id] = task
    task.add_done_callback(lambda _t: state.turn_tasks.pop(turn_id, None))
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
        logger.info("dropping op that fails scene_op schema: %s", exc.message)
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
    await send("turn_started", turn_id, {"utterance_id": utterance_id, "turn_id": turn_id}, utterance_id)
    bind = getattr(state.planner, "bind_tools", None)
    if bind is not None:
        try:
            bind(
                jobs=state.jobs,
                jpeg_b64=base64.b64encode(buf.jpeg).decode() if buf.jpeg else None,
                frame_id=(buf.envelope or {}).get("frame_id"),
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
    except Exception:
        logger.exception("planner failed for turn %d", turn_id)
        if turn_id not in state.cancelled_turns:
            await send("speak", turn_id, {"turn_id": turn_id, "text": SAY_MODEL_ERROR, "audio": None}, utterance_id)
        return
    if turn_id in state.cancelled_turns:
        return

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
    audio_block, voice_gate = await _speak_audio(state, line)
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
    buf = state.utterances.setdefault(utterance_id, UtteranceBuffer())
    if len(buf.pcm) + len(pcm) > state.max_utterance_bytes:
        logger.info("utterance %s over cap; dropping chunk", utterance_id)
        return
    buf.pcm.extend(pcm)


async def _speak_audio(
    state: CoordinatorState, line: str
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
    except Exception:
        logger.exception("cloud speech failed")
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


def build_place_generated(*, job_id: str, turn_id: int, stage_epoch: int, target: dict) -> dict:
    op = {
        "op_id": new_ulid(),
        "turn_id": turn_id,
        "stage_epoch": stage_epoch,
        "kind": "place_generated",
        "drawing_id": None,
        "job_id": job_id,
        "target": target,
    }
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
    turn_id = max(state.turn_id, 1)
    target = job.get("target")
    if not isinstance(target, dict):
        await complete_final_fn({"status": "failed"})
        return
    op = build_place_generated(
        job_id=job_id,
        turn_id=turn_id,
        stage_epoch=stage_epoch,
        target=target,
    )
    ack = await send(op)
    if ack is None:
        await complete_final_fn({"status": "failed"})
        return
    await complete_final_fn(ack)
