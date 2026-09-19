"""Coordinator offline tests (Task 6).

In-process fake WebSocket tests only: no bound ports, no live service,
no yibuapi calls. Covers hello/hello_ok, ping/pong, one hardcoded
scene_op mark after the first valid frame (hello alone never emits a
mark), session/stage fencing, clock-skew logging, and defensive
invalid input.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
import unittest

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


def make_frame(
    session_id: str | None = None,
    stage_epoch: int | None = None,
    t_unix_ns: int | None = None,
) -> dict:
    envelope = load_fixture("valid", "capture_envelope.json")
    if stage_epoch is not None:
        envelope["stage_epoch"] = stage_epoch
    if t_unix_ns is not None:
        envelope["t_unix_ns"] = t_unix_ns
    return {
        "v": 1,
        "type": "frame",
        "session_id": session_id,
        "turn_id": 0,
        "utterance_id": None,
        "payload": {"envelope": envelope},
    }


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


async def run_session(
    messages: list[object], state: CoordinatorState | None = None
) -> tuple[DummyWs, CoordinatorState]:
    """Feed messages through handle_connection, return (ws, state)."""
    active = state if state is not None else CoordinatorState()
    ws = DummyWs()
    task = asyncio.create_task(handle_connection(ws, active))
    try:
        for message in messages:
            await ws.inject(message)
        await asyncio.sleep(0.2)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return ws, active


class CoordinatorTests(unittest.TestCase):
    def test_hello_ok_assigns_session(self) -> None:
        async def scenario() -> None:
            state = CoordinatorState()
            ws = DummyWs()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                hello_ok = await wait_for(ws, lambda m: m["type"] == "hello_ok")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            validate_instance("message", hello_ok)
            self.assertEqual(hello_ok["v"], 1)
            session_id = hello_ok["payload"]["session_id"]
            self.assertIsInstance(session_id, str)
            self.assertEqual(len(session_id), 26)
            self.assertEqual(state.session_id, session_id)
            self.assertIsInstance(hello_ok["payload"]["laptop_t_unix_ns"], int)

        asyncio.run(scenario())

    def test_ping_replies_pong(self) -> None:
        async def scenario() -> dict:
            ws, _ = await run_session([make_ping()])
            (pong,) = [m for m in ws.sent if m["type"] == "pong"]
            return pong

        pong = asyncio.run(scenario())
        validate_instance("message", pong)
        self.assertEqual(pong["v"], 1)

    def test_frame_triggers_one_hardcoded_mark(self) -> None:
        async def scenario():
            envelope = load_fixture("valid", "capture_envelope.json")
            ws, state = await run_session([make_frame()])
            marks = [m for m in ws.sent if m["type"] == "scene_op"]
            return ws, state, envelope, marks

        ws, state, envelope, marks = asyncio.run(scenario())
        self.assertEqual(len(marks), 1)
        mark_msg = marks[0]
        validate_instance("message", mark_msg)
        self.assertEqual(mark_msg["v"], 1)
        op = mark_msg["payload"]
        validate_instance("scene_op", op)
        self.assertEqual(op["kind"], "mark")
        self.assertEqual(op["turn_id"], 1)
        self.assertEqual(op["stage_epoch"], envelope["stage_epoch"])
        self.assertEqual(op["target"]["type"], "capture_hint")
        self.assertEqual(op["target"]["frame_id"], envelope["frame_id"])
        self.assertEqual(op["motion"], {"kind": "pulse", "period_s": 1.2})
        self.assertIn(op["op_id"], state.pending_ops)

    def test_hello_alone_sends_no_scene_op(self) -> None:
        """Production starvation guard: hello returns only hello_ok."""

        async def scenario():
            return await run_session([make_hello()])

        ws, state = asyncio.run(scenario())
        self.assertEqual([m["type"] for m in ws.sent], ["hello_ok"])
        self.assertFalse(state.mark_sent)
        self.assertEqual(state.pending_ops, {})

    def test_production_sequence_hello_then_frame_emits_one_real_mark(self) -> None:
        """hello, then first valid frame emits exactly one mark on the
        real envelope frame_id/stage_epoch; repeated frames add none."""

        async def scenario():
            envelope = load_fixture("valid", "capture_envelope.json")
            frame = make_frame()
            ws, state = await run_session(
                [make_hello(), frame, copy.deepcopy(frame)]
            )
            return ws, state, envelope

        ws, state, envelope = asyncio.run(scenario())
        # hello_ok first, then exactly one mark, then silence.
        self.assertEqual(
            [m["type"] for m in ws.sent], ["hello_ok", "scene_op"]
        )
        mark_msg = ws.sent[1]
        validate_instance("message", mark_msg)
        op = mark_msg["payload"]
        validate_instance("scene_op", op)
        self.assertEqual(op["kind"], "mark")
        self.assertEqual(op["turn_id"], 1)
        self.assertEqual(op["stage_epoch"], envelope["stage_epoch"])
        self.assertEqual(op["target"]["type"], "capture_hint")
        self.assertEqual(op["target"]["frame_id"], envelope["frame_id"])
        self.assertEqual(op["motion"], {"kind": "pulse", "period_s": 1.2})
        self.assertIn(op["op_id"], state.pending_ops)
        self.assertTrue(state.mark_sent)

    def test_session_fencing_ignores_foreign_session_frame(self) -> None:
        async def scenario():
            ws = DummyWs()
            state = CoordinatorState()
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                hello_ok = await wait_for(ws, lambda m: m["type"] == "hello_ok")
                session_id = hello_ok["payload"]["session_id"]
                foreign = make_frame(session_id="00" + "0" * 24)
                self.assertNotEqual(foreign["session_id"], session_id)
                await ws.inject(foreign)
                await asyncio.sleep(0.15)
                before = list(ws.sent)
                fenced_envelope = state.last_envelope
                fenced_epoch = state.latest_stage_epoch
                await ws.inject(make_frame(session_id=session_id))
                await asyncio.sleep(0.15)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return ws, state, before, fenced_envelope, fenced_epoch

        ws, state, before, fenced_envelope, fenced_epoch = asyncio.run(scenario())
        # Foreign-session frame must not touch envelope state.
        self.assertIsNone(fenced_envelope)
        self.assertEqual(fenced_epoch, 0)
        # Matching-session frame is accepted into state.
        accepted = state.last_envelope
        assert accepted is not None
        self.assertEqual(
            accepted["frame_id"],
            load_fixture("valid", "capture_envelope.json")["frame_id"],
        )

    def test_stage_fencing_discards_older_epoch(self) -> None:
        async def scenario():
            now_ns = time.time_ns()
            first = make_frame(stage_epoch=5, t_unix_ns=now_ns)
            first_id = first["payload"]["envelope"]["frame_id"]
            stale = make_frame(stage_epoch=3, t_unix_ns=now_ns)
            stale["payload"]["envelope"]["frame_id"] = "01k5j8g0099q3m7b2d6h9n4r5v"
            ws, state = await run_session([first, stale])
            return ws, state, first_id

        _, state, first_id = asyncio.run(scenario())
        self.assertEqual(state.latest_stage_epoch, 5)
        latest = state.last_envelope
        assert latest is not None
        self.assertEqual(latest["frame_id"], first_id)
        self.assertEqual(latest["stage_epoch"], 5)

    def test_clock_skew_recorded_and_logged(self) -> None:
        async def scenario():
            with self.assertLogs("coordinator.server", level="WARNING") as logs:
                ws, state = await run_session([make_frame()])  # t_unix_ns=0
            return ws, state, logs.output

        _, state, output = asyncio.run(scenario())
        skew = state.last_clock_skew_ns
        assert skew is not None
        self.assertGreater(abs(skew), 2_000_000_000)
        self.assertTrue(any("clock_skew_ns" in line for line in output))

    def test_no_skew_when_clocks_agree(self) -> None:
        async def scenario():
            return await run_session([make_frame(t_unix_ns=time.time_ns())])

        _, state = asyncio.run(scenario())
        self.assertIsNone(state.last_clock_skew_ns)

    def test_invalid_input_ignored_without_crashing(self) -> None:
        async def scenario():
            bad_envelope = load_fixture("valid", "capture_envelope.json")
            del bad_envelope["frame_id"]
            messages: list[object] = [
                "this is not json",
                "",
                json.dumps({"v": 2, "type": "ping", "session_id": None,
                            "turn_id": 0, "utterance_id": None, "payload": {}}),
                json.dumps({"v": 1, "type": "nope", "session_id": None,
                            "turn_id": 0, "utterance_id": None, "payload": {}}),
                {"v": 1, "type": "frame", "session_id": None, "turn_id": 0,
                 "utterance_id": None, "payload": {"envelope": bad_envelope}},
                make_ping(),  # server must still be alive
            ]
            return await run_session(messages)

        ws, _ = asyncio.run(scenario())
        pongs = [m for m in ws.sent if m["type"] == "pong"]
        self.assertEqual(len(pongs), 1)
        self.assertFalse([m for m in ws.sent if m["type"] == "scene_op"])

    def test_outbound_wrappers_are_v1_json_text(self) -> None:
        async def scenario():
            return await run_session([make_hello(), make_ping(), make_frame()])

        ws, _ = asyncio.run(scenario())
        self.assertGreater(len(ws.sent), 0)
        for message in ws.sent:
            self.assertEqual(message["v"], 1)
            validate_instance("message", message)
        json.dumps(ws.sent)  # must be JSON-serializable text frames


if __name__ == "__main__":
    unittest.main()
