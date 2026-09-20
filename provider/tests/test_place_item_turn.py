from __future__ import annotations

import asyncio
import unittest

from coordinator.jobs import JobStore
from coordinator.listings import ListingMemory
from coordinator.planner import MAX_OPS_PER_TURN, YibuPlanner

_FRAME = "01k5j8g0008q3m7b2d6h9n4r5v"
_TARGET = {"type": "capture_hint", "frame_id": _FRAME}
_ARTIFACT = "01m2xbae3n81b4scq0k83teqjw"


class CoordinatorOpPlumbingTests(unittest.TestCase):
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

    def test_place_item_returns_result_and_records_op(self) -> None:
        planner = self._planner()
        result = asyncio.run(planner._dispatch_tool("place_item", {
            "name": "oak side table",
            "extent_m": [0.55, 0.40, 0.72],
            "target": _TARGET,
        }))
        self.assertNotIn("error", result)
        self.assertEqual(len(planner._coordinator_ops), 1)
        self.assertEqual(planner._coordinator_ops[0]["kind"], "place_generated")

    def test_refused_place_item_records_no_op(self) -> None:
        planner = self._planner()
        result = asyncio.run(planner._dispatch_tool("place_item", {
            "name": "oak side table", "target": _TARGET,
        }))
        self.assertEqual(result["error"], "invalid")
        self.assertEqual(planner._coordinator_ops, [])

    def test_bind_tools_resets_pending_ops(self) -> None:
        planner = self._planner()
        planner._coordinator_ops.append({"kind": "place_generated"})
        planner.bind_tools(jobs=JobStore(), jpeg_b64="", frame_id=_FRAME)
        self.assertEqual(planner._coordinator_ops, [])

    def test_coordinator_ops_lead_and_respect_the_cap(self) -> None:
        planner = self._planner()
        for index in range(4):
            planner._coordinator_ops.append({"kind": "place_generated", "index": index})
        merged = planner._merge_ops([], _FRAME)
        self.assertEqual(len(merged), MAX_OPS_PER_TURN)
        self.assertEqual([op["index"] for op in merged], [0, 1, 2])
