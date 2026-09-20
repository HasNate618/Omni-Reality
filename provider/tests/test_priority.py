"""Task 7: coordinator ACK bookkeeping + control-message priority (TDD).

In-process fake WebSocket tests only: no bound ports, no live service,
no yibuapi calls. Covers the Task 7 brief Step 1 contract:

- After the server sends `scene_op`, an inbound `ack` with
  `status=placed` marks that `op_id` complete (removed from
  `pending_ops`, recorded in `completed_ops`).
- An inbound `ack` with `status=rejected` records the outcome and the
  server never retries the same `op_id` (no second `scene_op`).
- `test_cancel_is_processed_before_queued_frame`: a `cancel` queued
  behind a large fake `frame` is still recorded. The inbound loop reads
  one JSON text frame at a time, so control (`ack`/`cancel`) travels as
  a separate WS message and is handled in arrival order without waiting
  on JPEG work; the Quest side sends `cancel`/`ack` without waiting to
  finish encoding a frame (`frame.jpeg_b64` may be omitted in slice 2,
  and the Quest send queue dequeues `cancel`/`ack` ahead of `frame`).
"""

from __future__ import annotations

import asyncio
import json
import unittest

from jsonschema import ValidationError

from coordinator.server import handle_connection, CoordinatorState
from protocol.validate import load_fixture, validate_instance


def make_hello(session_id: str | None = None) -> dict:
    return {
        "v": 1,
        "type": "hello",
        "session_id": session_id,
        "turn_id": 0,
        "utterance_id": None,
        "payload": {
            "device": "quest3s",
            "app": "QuestDemo",
            "os_version": "74",
            "capabilities": {"pca": True, "depth": True, "tts": True},
        },
    }


def make_frame(session_id: str | None = None, jpeg_size: int = 0) -> dict:
    envelope = load_fixture("valid", "capture_envelope.json")
    payload: dict = {"envelope": envelope}
    if jpeg_size > 0:
        # Large fake JPEG body beside (never inside) the envelope: control
        # messages must not wait on it.
        payload["jpeg_b64"] = "A" * jpeg_size
    return {
        "v": 1,
        "type": "frame",
        "session_id": session_id,
        "turn_id": 0,
        "utterance_id": None,
        "payload": payload,
    }


def make_ack(
    session_id: str | None,
    op: dict,
    status: str,
    reason: str | None = None,
) -> dict:
    placed = load_fixture("valid", "placement_ack_placed.json")
    if status == "placed":
        payload = {
            **placed,
            "op_id": op["op_id"],
            "turn_id": op["turn_id"],
            "stage_epoch": op["stage_epoch"],
        }
    else:
        payload = {
            "op_id": op["op_id"],
            "turn_id": op["turn_id"],
            "stage_epoch": op["stage_epoch"],
            "drawing_id": None,
            "status": status,
            "reason": reason,
            "pin": None,
        }
    validate_instance("placement_ack", payload)
    message = {
        "v": 1,
        "type": "ack",
        "session_id": session_id,
        "turn_id": op["turn_id"],
        "utterance_id": None,
        "payload": payload,
    }
    validate_instance("message", message)
    return message


def make_cancel(session_id: str | None, op_id: str, turn_id: int = 1) -> dict:
    message = {
        "v": 1,
        "type": "cancel",
        "session_id": session_id,
        "turn_id": turn_id,
        "utterance_id": None,
        "payload": {"op_id": op_id},
    }
    validate_instance("message", message)
    return message


def make_ping(session_id: str | None = None) -> dict:
    return {
        "v": 1,
        "type": "ping",
        "session_id": session_id,
        "turn_id": 0,
        "utterance_id": None,
        "payload": {},
    }


class DummyWs:
    """In-process fake: send/recv only, no sockets."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, text: str) -> None:
        if isinstance(text, (bytes, bytearray)):
            text = bytes(text).decode("utf-8")
        self.sent.append(json.loads(text))

    async def recv(self) -> str:
        return await self._queue.get()

    async def inject(self, message: object) -> None:
        if isinstance(message, str):
            await self._queue.put(message)
        else:
            await self._queue.put(json.dumps(message))


async def wait_for(ws: DummyWs, predicate, timeout: float = 2.0) -> dict:
    async def _wait() -> dict:
        while True:
            for message in ws.sent:
                if predicate(message):
                    return message
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_wait(), timeout)


class AckPriorityTests(unittest.TestCase):
    def test_ack_placed_marks_op_complete(self) -> None:
        async def scenario():
            ws = DummyWs()
            state = CoordinatorState()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await ws.inject(make_frame())
                mark = await wait_for(ws, lambda m: m["type"] == "scene_op")
                op = mark["payload"]
                self.assertIn(op["op_id"], state.pending_ops)
                await ws.inject(
                    make_ack(mark["session_id"], op, status="placed")
                )
                await asyncio.sleep(0.15)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return ws, state, mark

        _, state, mark = asyncio.run(scenario())
        op_id = mark["payload"]["op_id"]
        self.assertNotIn(op_id, state.pending_ops)
        self.assertIn(op_id, state.completed_ops)
        self.assertEqual(state.completed_ops[op_id]["status"], "placed")

    def test_ack_rejected_never_retries_same_op(self) -> None:
        async def scenario():
            ws = DummyWs()
            state = CoordinatorState()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await ws.inject(make_frame())
                mark = await wait_for(ws, lambda m: m["type"] == "scene_op")
                op = mark["payload"]
                await ws.inject(
                    make_ack(
                        mark["session_id"], op,
                        status="rejected", reason="no_surface",
                    )
                )
                await asyncio.sleep(0.15)
                # A later frame must not resurrect the rejected op.
                await ws.inject(make_frame(session_id=mark["session_id"]))
                await asyncio.sleep(0.15)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return ws, state, mark

        ws, state, mark = asyncio.run(scenario())
        op_id = mark["payload"]["op_id"]
        self.assertNotIn(op_id, state.pending_ops)
        self.assertIn(op_id, state.completed_ops)
        self.assertEqual(state.completed_ops[op_id]["status"], "rejected")
        marks = [m for m in ws.sent if m["type"] == "scene_op"]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["payload"]["op_id"], op_id)

    def test_cancel_is_processed_before_queued_frame(self) -> None:
        async def scenario():
            ws = DummyWs()
            state = CoordinatorState()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await ws.inject(make_frame())
                mark = await wait_for(ws, lambda m: m["type"] == "scene_op")
                op = mark["payload"]
                # Large fake frame queued first, then the cancel: the
                # handler must still record the cancel (arrival order,
                # one JSON text frame at a time, no control starvation).
                await ws.inject(
                    make_frame(session_id=mark["session_id"], jpeg_size=1 << 20)
                )
                await ws.inject(make_cancel(mark["session_id"], op["op_id"]))
                await asyncio.sleep(0.3)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return ws, state, mark

        _, state, mark = asyncio.run(scenario())
        op_id = mark["payload"]["op_id"]
        self.assertNotIn(op_id, state.pending_ops)
        self.assertIn(op_id, state.cancelled_op_ids)

    def test_unknown_ack_ignored_without_crashing(self) -> None:
        async def scenario():
            ws = DummyWs()
            state = CoordinatorState()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await ws.inject(make_frame())
                mark = await wait_for(ws, lambda m: m["type"] == "scene_op")
                op = dict(mark["payload"])
                op["op_id"] = "01k5j8g0099q3m7b2d6h9n4r5v"
                await ws.inject(
                    make_ack(mark["session_id"], op, status="placed")
                )
                await ws.inject(make_ping(session_id=mark["session_id"]))
                await asyncio.sleep(0.15)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return ws, state, mark

        ws, state, mark = asyncio.run(scenario())
        # The real op is still pending; the unknown ack recorded nothing.
        self.assertIn(mark["payload"]["op_id"], state.pending_ops)
        self.assertEqual(state.completed_ops, {})
        pongs = [m for m in ws.sent if m["type"] == "pong"]
        self.assertEqual(len(pongs), 1)

    def test_invalid_turn_rejection_ack_is_schema_valid_and_settles_op(self) -> None:
        # Fix-round-1 regression: a mark rejected for a missing/zero turn
        # must still settle server-side. The fixed Quest client normalizes
        # the ACK turn to minimum 1 (EnqueueAck: 1 when op.TurnId < 1)
        # because placement_ack requires turn_id >= 1.
        ulid = load_fixture("valid", "placement_ack_placed.json")["op_id"]
        zero_payload = {
            "op_id": ulid,
            "turn_id": 0,
            "stage_epoch": 1,
            "drawing_id": None,
            "status": "rejected",
            "reason": "invalid",
            "pin": None,
        }
        zero_msg = {
            "v": 1,
            "type": "ack",
            "session_id": None,
            "turn_id": 0,
            "utterance_id": None,
            "payload": zero_payload,
        }
        # The wrapper allows turn 0, so the drop happens one layer down.
        validate_instance("message", zero_msg)
        with self.assertRaises(ValidationError):
            validate_instance("placement_ack", zero_payload)

        async def scenario():
            ws = DummyWs()
            state = CoordinatorState()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await ws.inject(make_frame())
                mark = await wait_for(ws, lambda m: m["type"] == "scene_op")
                op = mark["payload"]
                sess = mark["session_id"]
                # Pre-fix client form (turn 0): dropped, op lingers.
                dropped = dict(zero_msg)
                dropped["session_id"] = sess
                dropped["payload"] = dict(zero_payload)
                dropped["payload"]["op_id"] = op["op_id"]
                dropped["payload"]["stage_epoch"] = op["stage_epoch"]
                await ws.inject(dropped)
                await asyncio.sleep(0.15)
                dropped_pending = op["op_id"] in state.pending_ops
                dropped_completed = dict(state.completed_ops)
                # Fixed EnqueueAck form (turn normalized to 1): settles.
                normalized = {
                    "v": 1,
                    "type": "ack",
                    "session_id": sess,
                    "turn_id": 1,
                    "utterance_id": None,
                    "payload": {
                        "op_id": op["op_id"],
                        "turn_id": 1,
                        "stage_epoch": op["stage_epoch"],
                        "drawing_id": None,
                        "status": "rejected",
                        "reason": "invalid",
                        "pin": None,
                    },
                }
                validate_instance("message", normalized)
                validate_instance("placement_ack", normalized["payload"])
                await ws.inject(normalized)
                await asyncio.sleep(0.15)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return state, op, dropped_pending, dropped_completed

        state, op, dropped_pending, dropped_completed = asyncio.run(scenario())
        self.assertTrue(dropped_pending)
        self.assertEqual(dropped_completed, {})
        self.assertNotIn(op["op_id"], state.pending_ops)
        self.assertEqual(state.completed_ops[op["op_id"]]["status"], "rejected")
        self.assertEqual(state.completed_ops[op["op_id"]]["reason"], "invalid")


if __name__ == "__main__":
    unittest.main()
