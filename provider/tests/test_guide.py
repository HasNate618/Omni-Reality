"""Offline tutorial planning/identity/control invariants (no gateway calls)."""
import copy
import json
import unittest
from unittest import mock

from coordinator.guide import GuidePlan, GuideSession, classify_guide_command
from coordinator.planner import YibuPlanner


def desk_plan():
    return {"title": "Organize your desk", "objects": [
        {"id": "laptop", "label": "Laptop", "u": .52, "v": .55},
        {"id": "mug", "label": "Mug", "u": .78, "v": .63},
        {"id": "notebook", "label": "Notebook", "u": .23, "v": .71}],
        "steps": [
            {"index": 0, "instruction": "Move your mug to the right of the laptop.", "highlight": ["mug", "laptop"]},
            {"index": 1, "instruction": "Place your notebook to the left of the laptop.", "highlight": ["notebook", "laptop"]},
            {"index": 2, "instruction": "Clear the area in front of the laptop.", "highlight": ["laptop"]}]}


class GuideTests(unittest.TestCase):
    def test_three_step_flow_preserves_identity_and_finishes(self):
        plan = GuidePlan.from_dict(desk_plan())
        session = GuideSession.from_plan(plan, "test-guide")
        self.assertEqual(session.objects, {"laptop": 1, "mug": 2, "notebook": 3})
        first = session.step_payload()
        self.assertEqual(first["active_obj_ids"], [2, 1])
        self.assertEqual(session.repeat(), first)
        self.assertEqual(session.current_step, 0)
        self.assertEqual(session.advance()["active_obj_ids"], [3, 1])
        self.assertEqual(session.advance()["active_obj_ids"], [1])
        self.assertIsNone(session.advance())
        self.assertFalse(session.active)
        self.assertIsNone(session.repeat())
        self.assertIsNone(session.advance())
        self.assertEqual([x["label"] for x in plan.tracking_targets], ["Laptop", "Mug", "Notebook"])

    def test_stop_is_terminal(self):
        session = GuideSession.from_plan(GuidePlan.from_dict(desk_plan()))
        session.stop()
        self.assertIsNone(session.advance())
        self.assertIsNone(session.step_payload())

    def test_validation_rejects_unsafe_or_unbounded_plan(self):
        mutations = [
            lambda p: p["objects"].append({"id": "extra", "label": "Extra", "u": 0, "v": 0}),
            lambda p: p["objects"][0].update(u=float("nan")),
            lambda p: p["objects"][0].update(u=float("inf")),
            lambda p: p["objects"][0].update(u=True),
            lambda p: p["objects"][0].update(u=320),
            lambda p: p["objects"][0].update(world_position=[1, 2, 3]),
            lambda p: p["objects"][1].update(id="laptop"),
            lambda p: p["objects"][1].update(u=.52, v=.55),
            lambda p: p["steps"][0].update(highlight=["unknown"]),
            lambda p: p["steps"][0].update(highlight=["mug", "mug"]),
            lambda p: p["steps"][0].update(instruction=" " * 240 + "Go."),
            lambda p: p["steps"][0].update(index=True),
            lambda p: p["steps"][1].update(index=4),
            lambda p: p["steps"].extend(copy.deepcopy(p["steps"]) * 3),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                raw = desk_plan()
                mutate(raw)
                with self.assertRaises(ValueError):
                    GuidePlan.from_dict(raw)

    def test_commands_are_whole_utterances(self):
        for phrase in ["next", "Okay, next.", "Done.", "Hey Omni, next step please"]:
            self.assertEqual(classify_guide_command(phrase), "next")
        self.assertEqual(classify_guide_command("repeat"), "repeat")
        self.assertEqual(classify_guide_command("stop the guide"), "stop")
        for phrase in ["what is next to my mug", "don't stop", "not done", "", "stopwatch"]:
            self.assertEqual(classify_guide_command(phrase), "unknown")


class GuidePlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_multimodal_request_returns_guide_and_ordered_seeds(self):
        planner = YibuPlanner(tracking=True)
        reply = json.dumps({"heard": "show me how to organize this desk", "guide": desk_plan()})
        with mock.patch.object(planner, "_call", return_value=(reply, {"call_id": "test"})) as call:
            result = await planner.plan(pcm=b'\0' * 100, jpeg=b'image', envelope={"frame_id": "frame"}, context=[])
        self.assertIsNotNone(result.guide_plan)
        self.assertEqual(result.tracking_targets, result.guide_plan.tracking_targets)
        self.assertEqual(result.text, "")
        content = call.call_args.args[0][1]["content"]
        self.assertIn("image_url", [part["type"] for part in content])
        self.assertIn("input_audio", [part["type"] for part in content])

    async def test_reject_guide_without_captured_image_or_valid_plan(self):
        for jpeg, envelope, raw in [(None, None, desk_plan()), (b'image', {}, desk_plan()), (b'image', {"frame_id": "f"}, {"title": "bad"})]:
            planner = YibuPlanner(tracking=True)
            with mock.patch.object(planner, "_call", return_value=(json.dumps({"guide": raw}), {})):
                result = await planner.plan(pcm=b'\0' * 100, jpeg=jpeg, envelope=envelope, context=[])
            self.assertIsNone(result.guide_plan)
            self.assertEqual(result.tracking_targets, [])
            self.assertIn("try again", result.text)

    async def test_ordinary_tracking_does_not_start_guide(self):
        planner = YibuPlanner(tracking=True)
        reply = {"heard": "track mug", "track": [{"label": "Mug", "type": "image_point", "u": .5, "v": .5}]}
        with mock.patch.object(planner, "_call", return_value=(json.dumps(reply), {})):
            result = await planner.plan(pcm=b'\0' * 100, jpeg=b'image', envelope={"frame_id": "f"}, context=[])
        self.assertIsNone(result.guide_plan)
        self.assertEqual(len(result.tracking_targets), 1)

    async def test_command_transcribes_only_with_audited_purpose(self):
        planner = YibuPlanner(tracking=True)
        with mock.patch('yibu_http.require_api_key', return_value="fake"), mock.patch('yibu_http.chat_completion', return_value=('{"heard":"Okay, next."}', {}, {})) as call:
            self.assertEqual(await planner.guide_command(pcm=b'\0' * 100), "Okay, next.")
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs['purpose'], 'guide-command')
        content = kwargs['messages'][1]['content']
        self.assertIn('input_audio', [part['type'] for part in content])
        self.assertNotIn('image_url', [part['type'] for part in content])
        self.assertNotIn('tools', kwargs)
