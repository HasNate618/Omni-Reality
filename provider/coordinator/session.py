"""Per-connection coordinator session state (Task 6).

Fencing rules:
- Session fencing: once a session_id is established, inbound messages
  carrying a different non-null session_id are ignored.
- Stage fencing: capture envelopes with a stage_epoch older than the
  latest accepted epoch are discarded (TrackingOrigin remaps only move
  the epoch forward).
"""

from __future__ import annotations


class CoordinatorState:
    """Mutable state for one coordinator connection."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.turn_id: int = 0
        self.latest_stage_epoch: int = 0
        self.last_envelope: dict | None = None
        self.pending_ops: dict[str, dict] = {}
        self.completed_ops: dict[str, dict] = {}
        self.cancelled_op_ids: list[str] = []
        self.mark_sent: bool = False
        self.last_clock_skew_ns: int | None = None

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

    def cancel_op(self, op_id: str) -> None:
        """Record a Quest cancel for a pending op; it is never retried."""
        self.pending_ops.pop(op_id, None)
        if op_id not in self.cancelled_op_ids:
            self.cancelled_op_ids.append(op_id)

    def is_op_settled(self, op_id: str) -> bool:
        """True once an op is acked or cancelled (dedupe seam)."""
        return op_id in self.completed_ops or op_id in self.cancelled_op_ids
