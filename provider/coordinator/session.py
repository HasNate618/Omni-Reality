"""Per-connection coordinator session state (Task 6).

Fencing rules:
- Session fencing: once a session_id is established, inbound messages
  carrying a different non-null session_id are ignored.
- Stage fencing: capture envelopes with a stage_epoch older than the
  latest accepted epoch are discarded (TrackingOrigin remaps only move
  the epoch forward).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coordinator.artifacts import ARTIFACT_PORT
from coordinator.jobs import JobStore


@dataclass
class UtteranceBuffer:
    """Mic PCM plus the frame captured for one open utterance."""

    pcm: bytearray = field(default_factory=bytearray)
    jpeg: bytes | None = None
    envelope: dict | None = None
    selected_frame_id: str | None = None
    frame_received: bool = False


class CoordinatorState:
    """Mutable state for one coordinator connection.

    `planner` None keeps the slice-2 behaviour (one hardcoded mark on the
    first frame). With a planner, frames feed utterances and voice turns
    run instead.
    """

    def __init__(self, planner: Any = None) -> None:
        self.planner = planner
        self.utterances: dict[str, UtteranceBuffer] = {}
        self.closed_utterances: set[str] = set()
        self.max_utterance_bytes: int = 30 * 16000 * 2  # 30 s cap
        self.turn_tasks: dict[int, asyncio.Task] = {}
        self.cancelled_turns: set[int] = set()
        self.ops_closed: dict[int, list[str]] = {}
        self.ack_events: dict[str, asyncio.Event] = {}
        self.context: list[dict] = []
        self.session_id: str | None = None
        self.turn_id: int = 0
        self.latest_stage_epoch: int = 0
        self.last_envelope: dict | None = None
        self.pending_ops: dict[str, dict] = {}
        self.completed_ops: dict[str, dict] = {}
        self.cancelled_op_ids: list[str] = []
        self.mark_sent: bool = False
        # Optional Sam2Bridge; existing mark/voice paths stay default.
        self.tracking: Any = None
        self.guide: Any = None
        self.guide_lock = asyncio.Lock()
        # Test seam: injected LiveSession factory (None = construct directly).
        self.live_factory: Any = None
        self.last_clock_skew_ns: int | None = None
        self.jobs = JobStore()
        # Cloud speech synth for speak.audio (None = caption-only degraded).
        # Server sets a live synth for yibu turns; tests inject fakes.
        self.synthesizer: Any | None = None
        # Persistent Live session for the realtime voice loop (None until
        # hello warms it). _live_turn is the in-flight turn, if any.
        # Any (not Optional): the live session or a test fake; None until warmed.
        self.live: Any = None
        self._live_turn: Any | None = None
        self._live_last_totals: dict[str, int] = {}
        # Last audio actually played on Quest (16 kHz mono s16le) + send time.
        # Feeds the echo gate: the mic re-hearing our own reply is dropped.
        self.last_speak_pcm: bytes | None = None
        self.last_speak_at: float = 0.0
        self.artifact_port: int = ARTIFACT_PORT
        # Set by run_server so `hello` can rebuild the planner for the mode
        # the headset's launcher picked. None in tests and headset-free tools,
        # where the CLI choice is the whole story.
        self.configure_mode: Any = None
        self.artifact_root: Path = Path(__file__).resolve().parent.parent / "artifacts" / "generated"
        self.clear_generation: int = 1

    def accepts_utterance(self, utterance_id: str) -> bool:
        # Bound open media and tombstones; after a very long session reconnect
        # rather than forgetting IDs and allowing old audio to become paid turns.
        return (utterance_id not in self.closed_utterances
                and len(self.closed_utterances) < 4096
                and (utterance_id in self.utterances or len(self.utterances) < 4))

    async def clear_voice(self) -> None:
        tasks = list(self.turn_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.turn_tasks.clear()
        if self.guide is not None:
            self.guide.stop()
            self.guide = None
        if self.tracking is not None:
            await self.tracking.reset()
        self.closed_utterances.update(self.utterances)
        self.utterances.clear()
        self.context.clear()
        self.last_envelope = None
        self.ack_events.clear()
        self.pending_ops.clear()

    def is_session_allowed(self, incoming: str | None) -> bool:
        """True unless an established session is contradicted."""
        if self.session_id is None or incoming is None:
            return True
        return incoming == self.session_id

    def is_epoch_fresh(self, stage_epoch: int) -> bool:
        """True unless the envelope is older than the latest accepted."""
        return stage_epoch >= self.latest_stage_epoch

    def accept_envelope(self, envelope: dict) -> None:
        """Record an accepted envelope and advance the stage epoch."""
        self.latest_stage_epoch = envelope["stage_epoch"]
        self.last_envelope = envelope

    def complete_op(self, op_id: str, ack: dict) -> None:
        """Mark a pending op complete (Task 7 ACK bookkeeping)."""
        self.pending_ops.pop(op_id, None)
        self.completed_ops[op_id] = ack
        event = self.ack_events.pop(op_id, None)
        if event is not None:
            event.set()

    def cancel_op(self, op_id: str) -> None:
        """Record a Quest cancel for a pending op; it is never retried."""
        self.pending_ops.pop(op_id, None)
        if op_id not in self.cancelled_op_ids:
            self.cancelled_op_ids.append(op_id)

    def is_op_settled(self, op_id: str) -> bool:
        """True once an op is acked or cancelled (dedupe seam)."""
        return op_id in self.completed_ops or op_id in self.cancelled_op_ids
