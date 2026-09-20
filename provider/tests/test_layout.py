"""Layout mode offline tests: canned cart -> three valid place_box ops.

No network and no key: the canned path is the one that has to work when the
key is dead, so every test here exercises it. The model path is covered by
injecting a fake `complete_fn`, never by calling out.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from coordinator import layout
from coordinator.layout import (
    LayoutPlanner,
    apply_model_slots,
    box_op,
    canned_ops,
    load_cart,
)
from coordinator.server import make_planner
from protocol.ids import new_ulid
from protocol.validate import validate_instance
from tests.test_turn import ONE_SECOND, Session, run


def stamp(model_op: dict, turn_id: int = 1, stage_epoch: int = 0) -> dict:
    """What turn._to_scene_op does before the op goes on the wire."""
    return {"op_id": new_ulid(), "turn_id": turn_id, "stage_epoch": stage_epoch,
            "drawing_id": None, **model_op}


class CartTests(unittest.TestCase):
    def test_shipped_cart_loads_with_three_items(self) -> None:
        cart = load_cart()
        self.assertEqual(len(cart["items"]), 3)
        for item in cart["items"]:
            for axis in ("w", "d", "h"):
                self.assertGreater(item["listing_mm"][axis], 0)

    def test_missing_cart_is_empty_not_an_exception(self) -> None:
        self.assertEqual(load_cart(Path("/nonexistent/cart.json")), {})

    def test_malformed_cart_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cart.json"
            path.write_text("{ not json")
            self.assertEqual(load_cart(path), {})
            path.write_text('{"items": "nope"}')
            self.assertEqual(load_cart(path), {})


class BoxOpTests(unittest.TestCase):
    def test_canned_ops_all_validate_as_scene_ops(self) -> None:
        ops = canned_ops(load_cart())
        self.assertEqual(len(ops), 3)
        for op in ops:
            validate_instance("scene_op", stamp(op))

    def test_millimetres_become_metres(self) -> None:
        op = box_op({"id": "x", "label": "sofa", "listing_mm": {"w": 1800, "d": 900, "h": 830},
                     "slot": {"dx": 1.0, "dz": 0.5, "yaw_deg": 0}})
        self.assertAlmostEqual(op["size_m"]["w"], 1.8)
        self.assertAlmostEqual(op["size_m"]["d"], 0.9)
        self.assertAlmostEqual(op["size_m"]["h"], 0.83)

    def test_implausible_or_missing_dimensions_are_dropped(self) -> None:
        slot = {"dx": 0.0, "dz": 0.0, "yaw_deg": 0}
        for bad in ({"w": 0, "d": 900, "h": 830},          # zero extent
                    {"w": 9000, "d": 900, "h": 830},        # 9 m sofa
                    {"w": "wide", "d": 900, "h": 830},      # not a number
                    {"w": 1800, "d": 900}):                 # missing axis
            self.assertIsNone(box_op({"id": "x", "listing_mm": bad, "slot": slot}), bad)

    def test_out_of_range_slot_is_dropped(self) -> None:
        mm = {"w": 1800, "d": 900, "h": 830}
        for bad in ({"dx": 99.0, "dz": 0.0, "yaw_deg": 0},
                    {"dx": 0.0, "dz": 0.0, "yaw_deg": 400},
                    {"dx": 0.0, "dz": 0.0}):
            self.assertIsNone(box_op({"id": "x", "listing_mm": mm, "slot": bad}), bad)

    def test_canned_layout_has_no_overlapping_footprints(self) -> None:
        """The default arrangement must look right before anyone drags it."""
        rects = []
        for op in canned_ops(load_cart()):
            t, s = op["target"], op["size_m"]
            w, d = (s["w"], s["d"]) if abs(t["yaw_deg"]) != 90 else (s["d"], s["w"])
            rects.append((t["dx"] - w / 2, t["dx"] + w / 2, t["dz"] - d / 2, t["dz"] + d / 2))
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                ax0, ax1, az0, az1 = rects[i]
                bx0, bx1, bz0, bz1 = rects[j]
                overlap = ax0 < bx1 and bx0 < ax1 and az0 < bz1 and bz0 < az1
                self.assertFalse(overlap, f"items {i} and {j} overlap")


class ModelSlotTests(unittest.TestCase):
    def test_valid_model_slots_are_applied(self) -> None:
        cart = load_cart()
        ops = apply_model_slots(cart, [{"id": "sofa", "dx": 1.2, "dz": 0.6, "yaw_deg": 0}])
        self.assertEqual(len(ops), 1)
        self.assertAlmostEqual(ops[0]["target"]["dx"], 1.2)
        validate_instance("scene_op", stamp(ops[0]))

    def test_unknown_id_rejects_the_whole_reply(self) -> None:
        cart = load_cart()
        self.assertIsNone(apply_model_slots(cart, [
            {"id": "sofa", "dx": 1.0, "dz": 0.5, "yaw_deg": 0},
            {"id": "hovercraft", "dx": 1.0, "dz": 1.5, "yaw_deg": 0}]))

    def test_junk_replies_reject(self) -> None:
        cart = load_cart()
        for bad in (None, [], "sofa", [{"id": "sofa"}], [{"id": "sofa", "dx": "left"}]):
            self.assertIsNone(apply_model_slots(cart, bad), bad)


def model_enabled():
    """The model path is opt-in; these tests are about what it does when on."""
    return mock.patch.dict(os.environ, {"OMNI_LAYOUT_MODEL": "1"})


class PlannerTests(unittest.TestCase):
    def test_make_planner_layout_is_canned_without_a_key(self) -> None:
        planner = make_planner("layout")
        self.assertEqual(type(planner).__name__, "LayoutPlanner")
        self.assertTrue(planner.layout)

    def test_plan_returns_three_ops_and_a_hedged_line(self) -> None:
        planner = LayoutPlanner()
        plan = run(planner.plan(pcm=b"", jpeg=None, envelope=None, context=[]))
        self.assertEqual(len(plan.ops), 3)
        self.assertIn("approximate", plan.text.lower())
        self.assertIsNone(plan.heard)

    def test_empty_cart_says_so_instead_of_placing(self) -> None:
        planner = LayoutPlanner(cart={})
        plan = run(planner.plan(pcm=b"", jpeg=None, envelope=None, context=[]))
        self.assertEqual(plan.ops, [])
        self.assertEqual(plan.text, layout.SAY_NO_CART)

    def test_model_reply_is_used_when_it_parses(self) -> None:
        async def fake_complete(messages, tools_enabled):
            return {"choices": [{"message": {"content":
                    '[{"id":"sofa","dx":1.5,"dz":0.7,"yaw_deg":10}]'}}]}

        with model_enabled():
            planner = LayoutPlanner(complete_fn=fake_complete)
            plan = run(planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=None, context=[]))
        self.assertEqual(len(plan.ops), 1)
        self.assertAlmostEqual(plan.ops[0]["target"]["dx"], 1.5)

    def test_model_prose_falls_back_to_canned(self) -> None:
        async def chatty(messages, tools_enabled):
            return {"choices": [{"message": {"content": "Sure! I'd put the sofa by the window."}}]}

        with model_enabled():
            planner = LayoutPlanner(complete_fn=chatty)
            plan = run(planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=None, context=[]))
        self.assertEqual(len(plan.ops), 3)

    def test_slow_model_does_not_hold_the_turn(self) -> None:
        """A hung gateway must lose to the canned arrangement.

        The shared HTTP client waits up to 300 s. On the headset this showed
        up as pressing the trigger and nothing happening at all: the turn had
        started and was still waiting on the model.
        """
        async def molasses(messages, tools_enabled):
            await asyncio.sleep(30)
            return {"choices": [{"message": {"content": "[]"}}]}

        planner = LayoutPlanner(complete_fn=molasses)
        started = time.monotonic()
        with model_enabled():
            plan = run(planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=None, context=[]))
        elapsed = time.monotonic() - started
        self.assertEqual(len(plan.ops), 3)
        self.assertLess(elapsed, layout.MODEL_TIMEOUT_S + 1.5)

    def test_model_failure_falls_back_to_canned(self) -> None:
        async def boom(messages, tools_enabled):
            raise RuntimeError("gateway down")

        with model_enabled():
            planner = LayoutPlanner(complete_fn=boom)
            plan = run(planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=None, context=[]))
        self.assertEqual(len(plan.ops), 3)


    def test_model_is_off_by_default(self) -> None:
        """Measured 16-39 s on the real gateway: never on the critical path."""
        called = []

        async def should_not_run(messages, tools_enabled):
            called.append(1)
            return {"choices": [{"message": {"content": "[]"}}]}

        planner = LayoutPlanner(complete_fn=should_not_run)
        with mock.patch.dict(os.environ, {"OMNI_LAYOUT_MODEL": ""}):
            plan = run(planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=None, context=[]))
        self.assertEqual(called, [], "model was called without OMNI_LAYOUT_MODEL")
        self.assertEqual(len(plan.ops), 3)


class LayoutTurnTests(unittest.TestCase):
    def test_one_utterance_places_three_boxes_then_speaks(self) -> None:
        async def scenario():
            async with Session(LayoutPlanner()) as s:
                await s.utter()
                from tests.test_coordinator import wait_for
                await wait_for(s.ws, lambda m: m["type"] == "scene_op")
                await s.ack_ops("placed")
                await wait_for(s.ws, lambda m: m["type"] == "speak")
                return s

        s = run(scenario())
        ops = [m["payload"] for m in s.of("scene_op")]
        self.assertEqual(len(ops), 3)
        for op in ops:
            validate_instance("scene_op", op)
            self.assertEqual(op["kind"], "place_box")
            self.assertEqual(op["target"]["type"], "layout_slot")
        self.assertEqual(s.types()[0], "turn_started")
        self.assertIn("approximate", s.of("speak")[0]["payload"]["text"].lower())

    def test_layout_turn_is_not_diverted_into_tracking(self) -> None:
        """A real Sam2Bridge attached must not swallow a layout turn.

        Without the `layout` gate in turn.py every layout utterance became a
        tracking turn and no box was ever placed.
        """
        from coordinator.sam2_bridge import Sam2Bridge

        seeded = []

        async def scenario():
            async with Session(LayoutPlanner()) as s:
                bridge = Sam2Bridge("ws://127.0.0.1:1", lambda *a, **k: None)

                async def never(*a, **k):
                    seeded.append("seed")

                bridge.seed = never
                s.state.tracking = bridge
                await s.utter()
                from tests.test_coordinator import wait_for
                await wait_for(s.ws, lambda m: m["type"] == "scene_op")
                return s

        s = run(scenario())
        self.assertEqual(seeded, [], "layout turn was diverted into SAM 2")
        ops = [m["payload"] for m in s.of("scene_op")]
        self.assertEqual(len(ops), 3)
        self.assertTrue(all(op["kind"] == "place_box" for op in ops))


if __name__ == "__main__":
    unittest.main()
