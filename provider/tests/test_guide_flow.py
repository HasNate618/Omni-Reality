"""Offline tutorial proof over the coordinator and a local SAM 2 socket double."""
import asyncio
import json
import unittest

import websockets

from coordinator.guide import GuidePlan
from coordinator.planner import PlanResult
from coordinator.sam2_bridge import Sam2Bridge
from coordinator.server import CoordinatorState, _turn_sender, handle_text
from protocol.ids import new_ulid
from tests.test_coordinator import DummyWs, make_hello, wait_for
from tests.test_tracking import picture, frame_message, sam_double
from tests.test_turn import ONE_SECOND, audio_chunk, msg


DESK = {
    "title": "Organize your desk",
    "objects": [
        {"id": "laptop", "label": "Laptop", "u": .52, "v": .55},
        {"id": "mug", "label": "Mug", "u": .78, "v": .63},
        {"id": "notebook", "label": "Notebook", "u": .23, "v": .71},
    ],
    "steps": [
        {"index": 0, "instruction": "Move the mug right of the laptop.", "highlight": ["mug", "laptop"]},
        {"index": 1, "instruction": "Place the notebook left of the laptop.", "highlight": ["notebook", "laptop"]},
        {"index": 2, "instruction": "Clear the space in front of the laptop.", "highlight": ["laptop"]},
    ],
}


class GuidePlannerDouble:
    def __init__(self):
        self.plans = []
        self.commands = []
        self.heard = "next"
        self.command_gate = None

    async def plan(self, **kwargs):
        self.plans.append(kwargs)
        return PlanResult([], "", guide_plan=GuidePlan.from_dict(DESK))

    async def guide_command(self, *, pcm):
        self.commands.append(pcm)
        if self.command_gate:
            await self.command_gate.wait()
        return self.heard


class GuideFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.server = await websockets.serve(sam_double(self.requests), "127.0.0.1", 0)
        self.ws = DummyWs()
        self.planner = GuidePlannerDouble()
        self.state = CoordinatorState(self.planner)
        url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.state.tracking = Sam2Bridge(url, _turn_sender(self.ws, self.state))
        self.sequence = 0
        await self.dispatch(make_hello())

    async def asyncTearDown(self):
        await self.state.clear_voice()
        self.server.close()
        await self.server.wait_closed()

    async def dispatch(self, message):
        await handle_text(self.ws, self.state, json.dumps(message))

    async def frame(self):
        self.sequence += 1
        envelope, jpeg = picture(self.sequence)
        await self.dispatch(frame_message(envelope, jpeg))
        return envelope, jpeg

    async def utterance(self, heard=None, *, mode="ptt", wait=True):
        self.planner.heard = heard or "next"
        utterance_id = new_ulid()
        frame_id = None
        if self.state.guide is None:
            envelope, _ = await self.frame()
            frame_id = envelope["frame_id"]
        chunk = audio_chunk(ONE_SECOND)
        chunk["utterance_id"] = chunk["payload"]["utterance_id"] = utterance_id
        await self.dispatch(chunk)
        await self.dispatch(msg("utterance_end", {"frame_id": frame_id, "mode": mode}, utterance_id))
        tasks = list(self.state.turn_tasks.values())
        if wait:
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
        return utterance_id

    def messages(self, kind):
        return [m for m in self.ws.sent if m["type"] == kind]

    async def test_three_steps_repeat_unknown_and_completion_seed_only_once(self):
        initial_utterance = await self.utterance()
        guide_id = self.state.guide.guide_id
        generation = self.state.tracking.generation
        self.assertEqual(self.messages("guide_step")[-1]["payload"]["active_obj_ids"], [2, 1])
        kinds = [m["type"] for m in self.ws.sent]
        if "tracking_result" in kinds:
            self.assertLess(kinds.index("guide_step"), kinds.index("tracking_result"))
        self.assertEqual(self.planner.plans[0]["envelope"]["frame_id"], self.requests[0]["frame_id"])
        await self.utterance("repeat")
        self.assertEqual(self.state.guide.current_step, 0)
        await self.utterance("what is the weather?")
        self.assertEqual(self.state.guide.current_step, 0)
        await self.utterance("Okay, next.", mode="live")
        self.assertEqual(self.messages("guide_step")[-1]["payload"]["active_obj_ids"], [3, 1])
        envelope, _ = await self.frame()
        result = await wait_for(self.ws, lambda m: m["type"] == "tracking_result" and m["payload"]["frame_id"] == envelope["frame_id"])
        self.assertEqual(result["utterance_id"], initial_utterance)
        self.assertEqual(result["payload"]["generation"], generation)
        self.assertEqual([o["obj_id"] for o in result["payload"]["objects"]], [1, 2, 3])
        await self.utterance("Done.")
        self.assertEqual(self.messages("guide_step")[-1]["payload"]["active_obj_ids"], [1])
        await self.utterance("next")
        self.assertIsNone(self.state.guide)
        self.assertIsNone(self.state.tracking.task)
        self.assertEqual(len(self.planner.plans), 1)
        self.assertEqual(len([r for r in self.requests if r["clicks"]]), 1)
        self.assertEqual(self.messages("guide_finished")[-1]["payload"]["reason"], "completed")
        self.assertTrue(all(m["payload"]["guide_id"] == guide_id for m in self.messages("guide_step")))
        self.assertEqual([m["payload"]["step_index"] for m in self.messages("guide_step")], [0, 0, 1, 2])
        spoken = [m["payload"]["text"] for m in self.messages("speak")]
        self.assertEqual(spoken[:2], [DESK["steps"][0]["instruction"]] * 2)

    async def test_voice_stop_then_new_request_creates_fresh_guide(self):
        await self.utterance()
        old = self.state.guide.guide_id
        await self.utterance("stop")
        self.assertIsNone(self.state.guide)
        self.assertEqual(self.messages("guide_finished")[-1]["payload"]["reason"], "stopped")
        await self.utterance()
        self.assertNotEqual(self.state.guide.guide_id, old)
        self.assertEqual(len(self.planner.plans), 2)

    async def test_duplicate_end_does_not_advance_twice(self):
        await self.utterance()
        uid = await self.utterance("next")
        await self.dispatch(msg("utterance_end", {}, uid))
        self.assertEqual(self.state.guide.current_step, 1)
        self.assertEqual(len(self.planner.commands), 1)

    async def test_concurrent_next_commands_are_serialized(self):
        await self.utterance()
        self.planner.command_gate = asyncio.Event()
        await self.utterance("next", wait=False)
        await self.utterance("next", wait=False)
        self.planner.command_gate.set()
        await asyncio.wait_for(asyncio.gather(*list(self.state.turn_tasks.values())), 3)
        self.assertEqual(self.state.guide.current_step, 2)
        self.assertEqual([m["payload"]["step_index"] for m in self.messages("guide_step")], [0, 1, 2])

    async def test_cancel_fences_pending_transcription(self):
        await self.utterance()
        self.planner.command_gate = asyncio.Event()
        await self.utterance("next", wait=False)
        await self.dispatch(msg("cancel", {"turn_id": self.state.turn_id}))
        self.planner.command_gate.set()
        await asyncio.gather(*list(self.state.turn_tasks.values()), return_exceptions=True)
        self.assertIsNone(self.state.guide)
        self.assertEqual(len(self.messages("guide_step")), 1)

    async def test_origin_change_and_clear_end_guide(self):
        await self.utterance()
        envelope, jpeg = picture(2)
        envelope["stage_epoch"] += 1
        await self.dispatch(frame_message(envelope, jpeg))
        self.assertIsNone(self.state.guide)
        self.assertEqual(self.messages("guide_finished")[-1]["payload"]["reason"], "error")
        self.assertIsNone(self.state.tracking.task)

    async def test_clear_session_stops_tracker(self):
        await self.utterance()
        await self.dispatch(msg("clear_session", {}))
        self.assertIsNone(self.state.guide)
        self.assertIsNone(self.state.tracking.task)
        self.assertEqual(len(self.state.tracking.history.frames), 0)

    async def test_tracker_failure_clears_active_guide(self):
        await self.utterance()
        await self.state.tracking.status("error", "SAM 2 disconnected.")
        self.assertIsNone(self.state.guide)
        self.assertEqual(self.messages("guide_finished")[-1]["payload"]["reason"], "error")

    async def test_partial_seed_failure_never_activates_guide(self):
        partial = await websockets.serve(sam_double([], drop_after_first=True), "127.0.0.1", 0)
        try:
            self.state.tracking.url = f"ws://127.0.0.1:{partial.sockets[0].getsockname()[1]}"
            await self.utterance()
            self.assertIsNone(self.state.guide)
            self.assertEqual(self.messages("guide_step"), [])
            self.assertEqual(self.messages("speak"), [])
            self.assertIsNone(self.state.tracking.task)
            self.assertTrue(any(m["payload"]["state"] == "error" for m in self.messages("tracking_status")))
        finally:
            partial.close()
            await partial.wait_closed()

    async def test_step_precedes_masks_and_speech_even_when_synthesis_is_slow(self):
        synth_started, synth_release = asyncio.Event(), asyncio.Event()
        async def synth(line):
            synth_started.set()
            await synth_release.wait()
            return None
        self.state.synthesizer = synth
        await self.utterance(wait=False)
        await asyncio.wait_for(synth_started.wait(), 2)
        await wait_for(self.ws, lambda m: m["type"] == "tracking_result")
        kinds = [m["type"] for m in self.ws.sent]
        self.assertLess(kinds.index("guide_step"), kinds.index("tracking_result"))
        self.assertEqual(self.messages("speak"), [])
        synth_release.set()
        await asyncio.wait_for(asyncio.gather(*list(self.state.turn_tasks.values())), 2)
        self.assertEqual(self.messages("speak")[-1]["payload"]["text"], DESK["steps"][0]["instruction"])

    async def test_missing_command_credentials_preserve_current_step(self):
        await self.utterance()
        async def unavailable(**kwargs):
            raise SystemExit("Missing configuration")
        self.planner.guide_command = unavailable
        await self.utterance("next")
        self.assertEqual(self.state.guide.current_step, 0)
        self.assertIn("Say next", self.messages("speak")[-1]["payload"]["text"])

    async def test_late_cancel_from_previous_guide_does_not_stop_new_guide(self):
        await self.utterance()
        old_turn = self.state.tracking.turn_id
        await self.utterance("stop")
        await self.utterance()
        new_guide = self.state.guide
        await self.dispatch(msg("cancel", {"turn_id": old_turn}))
        self.assertIs(self.state.guide, new_guide)
        self.assertTrue(new_guide.active)
