"""Voice turn loop offline tests (spec §7).

In-process fake WebSocket with StubPlanner: no ports, no yibuapi. Covers
message order, the 0.5 s utterance floor, ACK-gated speech (placed,
rejected, stale, timeout, late ACK), op validation/capping, planner
failure, and cancel before the planner returns.
"""

from __future__ import annotations

import asyncio
import base64
import unittest
from unittest import mock

from coordinator import turn
from coordinator.planner import StubPlanner, accept_model_ops
from coordinator.server import CoordinatorState, UtteranceBuffer, handle_connection
from protocol.validate import load_fixture, validate_instance
from tests.test_coordinator import DummyWs, make_hello, wait_for
from voice.audio import pcm_to_wav_bytes, read_wav_pcm

UTT = "01k5j8g0038q3m7b2d6h9n4r5v"
def _voiced_second() -> bytes:
    import math as _math
    import struct as _struct
    return b"".join(_struct.pack("<h", int(8000 * _math.sin(2 * _math.pi * 300 * i / 16000)))
                       for i in range(16000))


# Speech-like energy: the server no-speech guard drops near-silent audio.
ONE_SECOND = _voiced_second()


def msg(msg_type: str, payload: dict, utterance_id: str | None = UTT, turn_id: int = 0) -> dict:
    return {
        "v": 1,
        "type": msg_type,
        "session_id": None,
        "turn_id": turn_id,
        "utterance_id": utterance_id,
        "payload": payload,
    }


def audio_chunk(pcm: bytes) -> dict:
    return msg(
        "audio_chunk",
        {
            "utterance_id": UTT,
            "t_unix_ns": 0,
            "audio": {
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
                "channels": 1,
                "data_b64": base64.b64encode(pcm).decode("ascii"),
            },
        },
    )


def frame() -> dict:
    return msg(
        "frame",
        {
            "utterance_id": UTT,
            "envelope": load_fixture("valid", "capture_envelope.json"),
            "jpeg_b64": base64.b64encode(b"\xff\xd8fakejpeg").decode("ascii"),
        },
    )


def ack(op: dict, status: str = "placed", reason: str | None = None) -> dict:
    payload = {
        "op_id": op["op_id"],
        "turn_id": op["turn_id"],
        "stage_epoch": op["stage_epoch"],
        "status": status,
        "reason": reason,
        "drawing_id": "01k5j8g002aq3m7b2d6h9n4r5v" if status == "placed" else None,
        "pin": "surface" if status == "placed" else None,
    }
    return msg("ack", payload, utterance_id=None, turn_id=op["turn_id"])


def mark(u: float = 0.5) -> dict:
    return {"kind": "mark", "target": {"type": "image_point", "u": u, "v": 0.5}}


class Session:
    """Drive one connection; ACK policy is a callback on each scene_op."""

    def __init__(self, planner: StubPlanner) -> None:
        self.state = CoordinatorState(planner=planner)
        self.ws = DummyWs()
        self.utterance_number = 0

    async def __aenter__(self) -> "Session":
        self.task = asyncio.create_task(handle_connection(self.ws, self.state))
        await self.ws.inject(make_hello())
        await wait_for(self.ws, lambda m: m["type"] == "hello_ok")
        return self

    async def __aexit__(self, *exc) -> None:
        for task in list(self.state.turn_tasks.values()):
            task.cancel()
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def utter(self, pcm: bytes = ONE_SECOND, with_frame: bool = True) -> None:
        # A new question has a new ID, just like Quest; replaying the same ID
        # is now correctly ignored rather than becoming a second paid turn.
        self.utterance_number += 1
        uid = UTT if self.utterance_number == 1 else f"{UTT}-{self.utterance_number}"
        messages = [frame()] if with_frame else []
        messages.extend([audio_chunk(pcm[: len(pcm) // 2]), audio_chunk(pcm[len(pcm) // 2 :]),
                         msg("utterance_end", {"utterance_id": uid, "t_unix_ns": 0})])
        for outgoing in messages:
            outgoing["utterance_id"] = uid
            outgoing["payload"]["utterance_id"] = uid
            await self.ws.inject(outgoing)

    def types(self) -> list[str]:
        return [m["type"] for m in self.ws.sent if m["type"] != "hello_ok"]

    def of(self, msg_type: str) -> list[dict]:
        return [m for m in self.ws.sent if m["type"] == msg_type]

    async def ack_ops(self, status: str = "placed", reason: str | None = None) -> list[dict]:
        ops = [m["payload"] for m in self.of("scene_op")]
        for op in ops:
            await self.ws.inject(ack(op, status, reason))
        return ops


def run(coro):
    return asyncio.run(coro)


async def _run_turn_for_test(state: CoordinatorState, utterance_id: str) -> list[dict]:
    """Drive one turn to completion without a WebSocket.

    start_turn owns the utterance floor and the background task; the tool
    bind happens inside _run_turn, which is what this exercises.
    """
    state.utterances[utterance_id] = UtteranceBuffer(pcm=bytearray(ONE_SECOND))
    sent: list[dict] = []

    async def send(msg_type: str, turn_id: int, payload: dict, uid: str | None = None) -> None:
        sent.append({"type": msg_type, "payload": payload})

    task = turn.start_turn(state, send, utterance_id)
    if task is None:
        raise AssertionError(f"turn not started for {utterance_id}")
    await task
    return sent


class TurnTests(unittest.TestCase):
    def test_happy_path_order_and_schema(self) -> None:
        planner = StubPlanner(text="That's your laptop.")

        async def scenario():
            async with Session(planner) as s:
                await s.utter()
                await wait_for(s.ws, lambda m: m["type"] == "scene_op")
                await s.ack_ops("placed")
                await wait_for(s.ws, lambda m: m["type"] == "speak")
                return s

        s = run(scenario())
        self.assertEqual(s.types(), ["turn_started", "scene_op", "speak"])
        for m in s.ws.sent:
            validate_instance("message", m)
        started, op_msg, speak = s.of("turn_started")[0], s.of("scene_op")[0], s.of("speak")[0]
        self.assertEqual(started["payload"], {"utterance_id": UTT, "turn_id": 1})
        op = op_msg["payload"]
        validate_instance("scene_op", op)
        self.assertEqual(op["turn_id"], 1)
        self.assertEqual(op["target"]["frame_id"], load_fixture("valid", "capture_envelope.json")["frame_id"])
        self.assertEqual(speak["payload"], {"turn_id": 1, "text": "That's your laptop.", "audio": None})
        self.assertEqual(speak["utterance_id"], UTT)
        self.assertEqual(planner.calls[0]["pcm_bytes"], len(ONE_SECOND))
        self.assertTrue(planner.calls[0]["jpeg"])
        self.assertEqual(s.state.ops_closed[1], [op["op_id"]])

    def test_short_utterance_makes_no_turn(self) -> None:
        planner = StubPlanner()

        async def scenario():
            async with Session(planner) as s:
                await s.utter(pcm=b"\x00\x00" * 4000)  # 0.25 s
                await asyncio.sleep(0.2)
                return s

        s = run(scenario())
        self.assertEqual(s.types(), [])
        self.assertEqual(planner.calls, [])

    def test_rejected_ack_does_not_claim_mark(self) -> None:
        async def scenario():
            async with Session(StubPlanner(text="Marked the laptop.")) as s:
                await s.utter()
                await wait_for(s.ws, lambda m: m["type"] == "scene_op")
                await s.ack_ops("rejected", "no_surface")
                return await wait_for(s.ws, lambda m: m["type"] == "speak")

        speak = run(scenario())
        self.assertEqual(speak["payload"]["text"], "I can't plant that on a surface.")

    def test_stale_ack_says_look_again(self) -> None:
        async def scenario():
            async with Session(StubPlanner()) as s:
                await s.utter()
                await wait_for(s.ws, lambda m: m["type"] == "scene_op")
                await s.ack_ops("stale", "superseded")
                return await wait_for(s.ws, lambda m: m["type"] == "speak")

        self.assertEqual(run(scenario())["payload"]["text"], "That moved, look again.")

    def test_ack_timeout_speaks_once_and_late_ack_is_silent(self) -> None:
        async def scenario():
            with mock.patch.object(turn, "ACK_TIMEOUT_S", 0.1):
                async with Session(StubPlanner(text="Marked.")) as s:
                    await s.utter()
                    await wait_for(s.ws, lambda m: m["type"] == "speak")
                    ops = await s.ack_ops("placed")  # late
                    await asyncio.sleep(0.2)
                    return s, ops

        s, ops = run(scenario())
        speaks = s.of("speak")
        self.assertEqual(len(speaks), 1)
        self.assertEqual(speaks[0]["payload"]["text"], turn.SAY_UNCONFIRMED)
        self.assertEqual(s.state.completed_ops[ops[0]["op_id"]]["status"], "placed")

    def test_ops_capped_at_three_and_invalid_dropped(self) -> None:
        bad = {"kind": "mark", "target": {"type": "world_point", "px": 1, "py": 0, "pz": 1}}
        planner = StubPlanner(ops_factory=lambda env: [bad, mark(0.1), mark(0.2), mark(0.3), mark(0.4)])

        async def scenario():
            async with Session(planner) as s:
                await s.utter()
                await wait_for(s.ws, lambda m: len(s.of("scene_op")) == 3)
                await s.ack_ops("placed")
                await wait_for(s.ws, lambda m: m["type"] == "speak")
                return s

        s = run(scenario())
        us = [m["payload"]["target"]["u"] for m in s.of("scene_op")]
        self.assertEqual(us, [0.1, 0.2, 0.3])
        self.assertEqual(len({m["payload"]["op_id"] for m in s.of("scene_op")}), 3)

    def test_all_ops_invalid_speaks_no_target(self) -> None:
        bad = {"kind": "mark", "target": {"type": "world_point", "px": 1, "py": 0, "pz": 1}}

        async def scenario():
            async with Session(StubPlanner(ops_factory=lambda env: [bad], text="Marked it!")) as s:
                await s.utter()
                return s, await wait_for(s.ws, lambda m: m["type"] == "speak")

        s, speak = run(scenario())
        self.assertEqual(s.of("scene_op"), [])
        self.assertEqual(speak["payload"]["text"], turn.SAY_NO_TARGET)

    def test_zero_op_turn_speaks_model_text(self) -> None:
        async def scenario():
            async with Session(StubPlanner(ops_factory=lambda env: [], text="It's a mug.")) as s:
                await s.utter(with_frame=False)
                return s, await wait_for(s.ws, lambda m: m["type"] == "speak")

        s, speak = run(scenario())
        self.assertEqual(s.types(), ["turn_started", "speak"])
        self.assertEqual(speak["payload"]["text"], "It's a mug.")

    def test_planner_error_speaks_once_no_ops(self) -> None:
        async def scenario():
            async with Session(StubPlanner(error=RuntimeError("gateway 502"))) as s:
                await s.utter()
                await wait_for(s.ws, lambda m: m["type"] == "speak")
                await asyncio.sleep(0.05)
                return s

        s = run(scenario())
        self.assertEqual(s.types(), ["turn_started", "speak"])
        self.assertEqual(s.of("speak")[0]["payload"]["text"], turn.SAY_MODEL_ERROR)

    def test_cancel_before_plan_returns_sends_nothing_more(self) -> None:
        async def scenario():
            async with Session(StubPlanner(delay_s=0.3)) as s:
                await s.utter()
                await wait_for(s.ws, lambda m: m["type"] == "turn_started")
                await s.ws.inject(msg("cancel", {"turn_id": 1, "reason": "barge_in"}, turn_id=1))
                await asyncio.sleep(0.5)
                return s

        s = run(scenario())
        self.assertEqual(s.types(), ["turn_started"])
        self.assertIn(1, s.state.cancelled_turns)

    def test_context_carries_into_next_turn(self) -> None:
        planner = StubPlanner(ops_factory=lambda env: [], text="Hi.")

        async def scenario():
            async with Session(planner) as s:
                await s.utter(with_frame=False)
                await wait_for(s.ws, lambda m: m["type"] == "speak")
                await s.utter(with_frame=False)
                await wait_for(s.ws, lambda m: m["type"] == "speak" and m["turn_id"] == 2)
                return s

        s = run(scenario())
        self.assertEqual(planner.calls[0]["context"], [])
        self.assertEqual(planner.calls[1]["context"][0]["said"], "Hi.")

    def test_default_state_keeps_hardcoded_mark(self) -> None:
        """No planner: audio/utterance_end are ignored; slice-2 mark path intact."""

        async def scenario():
            state, ws = CoordinatorState(), DummyWs()
            task = asyncio.create_task(handle_connection(ws, state))
            for m in (audio_chunk(ONE_SECOND), msg("utterance_end", {"utterance_id": UTT}), frame()):
                await ws.inject(m)
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return ws

        ws = run(scenario())
        self.assertEqual([m["type"] for m in ws.sent], ["scene_op"])

    def test_turn_binds_session_stores_into_the_planner(self) -> None:
        bound = {}

        class RecordingPlanner:
            def bind_tools(self, **kwargs):
                bound.update(kwargs)

            async def plan(self, **kwargs):
                from coordinator.planner import PlanResult

                return PlanResult(ops=[], text="ok")

        state = CoordinatorState(planner=RecordingPlanner())
        state.listings.record("oak side table", [0.55, 0.40, 0.72], "f1", "page")
        state.prebaked = {"oak side table": "01m2xbae3n81b4scq0k83teqjw"}
        asyncio.run(_run_turn_for_test(state, "utt-1"))
        self.assertIs(bound.get("listings"), state.listings)
        self.assertEqual(bound.get("prebaked"), state.prebaked)
        self.assertIs(bound.get("jobs"), state.jobs)


class AudioHelperTests(unittest.TestCase):
    def test_wav_roundtrip(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            path.write_bytes(pcm_to_wav_bytes(ONE_SECOND))
            self.assertEqual(read_wav_pcm(path), ONE_SECOND)

    def test_accept_model_ops_fills_frame_id(self) -> None:
        (op,) = accept_model_ops([mark()], "01k5j8g0008q3m7b2d6h9n4r5v")
        self.assertEqual(op["target"]["frame_id"], "01k5j8g0008q3m7b2d6h9n4r5v")
        self.assertEqual(accept_model_ops([mark()], None), [])


if __name__ == "__main__":
    unittest.main()
