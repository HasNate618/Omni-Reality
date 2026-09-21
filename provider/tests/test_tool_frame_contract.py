"""Regression tests for the frame contract that made every placement unplaceable.

The demo was impossible while 299 tests passed. Two independent defects:

1. The tool planner was only bound on the mode where Quest sends NO camera frame
   (`turn.py` gated the bind on `not perception_qa`, and Quest gates its capture on
   the same flag it read back), so every frame-anchored tool call was refused.
2. Even with a frame, `place_item`'s target must carry a `frame_id` matching the
   capture, and the model was never told that id -- a 26-char ULID minted on Quest
   and never shown to it.

Both were invisible because every test authored the frame and the frame_id by hand.
These tests deliberately do NOT: they exercise the case where the model supplies
only what a model can actually know.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from coordinator.jobs import JobStore
from coordinator.listings import ListingMemory
from coordinator.planner import YibuPlanner, fill_tool_args
from coordinator.turn import planner_binds_tools

_FRAME = "01k5j8g0008q3m7b2d6h9n4r5v"
_UNREACHABLE = "01k5j8g0008q3m7b2d6h9n4r5w"
_ARTIFACT = "01m2xbae3n81b4scq0k83teqjw"


class FillToolArgsTests(unittest.TestCase):
    def test_fills_a_frame_target_the_model_left_blank(self) -> None:
        args = fill_tool_args("place_item", {"target": {"type": "capture_hint"}}, _FRAME)
        self.assertEqual(args["target"]["frame_id"], _FRAME)

    def test_overwrites_a_frame_id_the_model_could_not_have_known(self) -> None:
        args = fill_tool_args(
            "place_item",
            {"target": {"type": "capture_hint", "frame_id": _UNREACHABLE}},
            _FRAME,
        )
        self.assertEqual(args["target"]["frame_id"], _FRAME)

    def test_fills_top_level_frame_id_for_a_tool_that_declares_one(self) -> None:
        args = fill_tool_args("inspect_objects", {"target": {"type": "image_point"}}, _FRAME)
        self.assertEqual(args["frame_id"], _FRAME)

    def test_leaves_a_non_frame_target_untouched(self) -> None:
        args = fill_tool_args("place_item", {"target": {"type": "surface"}}, _FRAME)
        self.assertNotIn("frame_id", args["target"])

    def test_no_capture_frame_is_a_no_op(self) -> None:
        original = {"target": {"type": "capture_hint"}}
        self.assertEqual(fill_tool_args("place_item", original, None), original)

    def test_does_not_mutate_the_callers_arguments(self) -> None:
        original = {"target": {"type": "capture_hint"}}
        fill_tool_args("place_item", original, _FRAME)
        self.assertNotIn("frame_id", original["target"])


class PlannerBindsToolsTests(unittest.TestCase):
    """The shared source of truth behind hello_ok's accepts_frame."""

    def test_tool_planner_binds_tools(self) -> None:
        planner = YibuPlanner(complete_fn=None, execute_fn=None)
        self.assertIs(planner_binds_tools(planner), True)

    def test_voice_only_planner_never_binds(self) -> None:
        planner = YibuPlanner(complete_fn=None, execute_fn=None, voice_only=True)
        self.assertIs(planner_binds_tools(planner), False)

    def test_perception_planner_never_binds(self) -> None:
        from coordinator import server

        planner = server.make_planner("yibu", perception_qa=True)
        self.assertIs(planner_binds_tools(planner), False)

    def test_no_planner_binds_nothing(self) -> None:
        self.assertIs(planner_binds_tools(None), False)


class PlaceItemWithoutAModelSuppliedFrameIdTests(unittest.TestCase):
    """The end-to-end shape of the bug: a real model call names no frame id."""

    def _planner(self) -> YibuPlanner:
        planner = YibuPlanner(complete_fn=None, execute_fn=None)
        planner.bind_tools(
            jobs=JobStore(),
            jpeg_b64="",
            frame_id=_FRAME,
            listings=ListingMemory(),
            prebaked={"oak side table": _ARTIFACT},
        )
        return planner

    def test_place_item_succeeds_without_the_model_naming_a_frame(self) -> None:
        planner = self._planner()
        result = asyncio.run(planner._dispatch_tool("place_item", {
            "name": "oak side table",
            "extent_m": [0.55, 0.40, 0.72],
            "target": {"type": "capture_hint"},
        }))
        self.assertNotIn("error", result, result)
        self.assertEqual(len(planner._coordinator_ops), 1)
        self.assertEqual(planner._coordinator_ops[0]["kind"], "place_generated")

    def test_hallucinated_frame_id_does_not_refuse_a_valid_placement(self) -> None:
        planner = self._planner()
        result = asyncio.run(planner._dispatch_tool("place_item", {
            "name": "oak side table",
            "extent_m": [0.55, 0.40, 0.72],
            "target": {"type": "capture_hint", "frame_id": _UNREACHABLE},
        }))
        self.assertNotIn("error", result, result)
        self.assertEqual(len(planner._coordinator_ops), 1)

    def test_a_turn_with_no_capture_frame_still_refuses(self) -> None:
        """The spec's refusal is preserved: no frame means no anchor, so no placement."""
        planner = YibuPlanner(complete_fn=None, execute_fn=None)
        planner.bind_tools(
            jobs=JobStore(),
            jpeg_b64="",
            frame_id=None,
            listings=ListingMemory(),
            prebaked={"oak side table": _ARTIFACT},
        )
        result = asyncio.run(planner._dispatch_tool("place_item", {
            "name": "oak side table",
            "extent_m": [0.55, 0.40, 0.72],
            "target": {"type": "capture_hint"},
        }))
        self.assertEqual(result["error"], "invalid")
        self.assertEqual(planner._coordinator_ops, [])


class ToolPathPromptTests(unittest.TestCase):
    """The tool path must TELL the model the tools exist.

    Before this, no prompt anywhere in the repo named a single tool. The system
    prompt said "Reply with ONLY one JSON object" and documented the legacy ops
    array, while `accept_model_ops` drops `place_generated` by design -- so the
    model had no instructed route to place a sized item, and every green test
    missed it because tests call the dispatcher directly rather than the model.
    """

    def _system_prompt(self, *, bind: bool) -> tuple[str, bool]:
        seen: dict[str, Any] = {}

        async def complete_fn(messages, tools_enabled):
            seen.setdefault("system", messages[0]["content"])
            seen["tools_enabled"] = tools_enabled
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        planner = YibuPlanner(complete_fn=complete_fn, execute_fn=None)
        if bind:
            planner.bind_tools(
                jobs=JobStore(),
                jpeg_b64="",
                frame_id=_FRAME,
                listings=ListingMemory(),
                prebaked={},
            )
        else:
            # The legacy path goes through self._call (the real network call),
            # not complete_fn, so stub it to capture without needing a key.
            def fake_call(messages):
                seen.setdefault("system", messages[0]["content"])
                return "ok", {}

            planner._call = fake_call
        asyncio.run(planner.plan(
            pcm=b"\x00\x00" * 8000,
            jpeg=b"\xff\xd8\xff\xe0",
            envelope={"frame_id": _FRAME, "stage_epoch": 0},
            context=[],
        ))
        return seen.get("system", ""), bool(seen.get("tools_enabled"))

    def test_tool_path_names_every_tool(self) -> None:
        system, tools_enabled = self._system_prompt(bind=True)
        self.assertTrue(tools_enabled, "the tool loop should run with tools enabled")
        for tool in ("place_item", "inspect_objects", "start_generation", "emit_scene_ops"):
            self.assertIn(tool, system)

    def test_tool_path_drops_the_legacy_ops_only_instruction(self) -> None:
        system, _ = self._system_prompt(bind=True)
        self.assertNotIn("Reply with ONLY one JSON object", system)

    def test_tool_path_forbids_guessing_a_size(self) -> None:
        """The rubric rests on stated sizes; the prompt must say never to invent one."""
        system, _ = self._system_prompt(bind=True)
        self.assertIn("ASK THE", system)
        self.assertIn("Never estimate", system)

    def test_legacy_path_keeps_the_ops_prompt(self) -> None:
        """No tools bound (offline/tests) still gets the JSON-ops contract."""
        system, _ = self._system_prompt(bind=False)
        self.assertIn("Reply with ONLY one JSON object", system)
        self.assertNotIn("place_item", system)


if __name__ == "__main__":
    unittest.main()
