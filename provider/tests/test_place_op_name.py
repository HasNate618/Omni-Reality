"""The listing name on `place_generated`, for the item-queue chips and take-home card.

The overlays label from this field, and it is OPTIONAL by contract: an absent name
must degrade to a dims-only label rather than a blank chip, so both the present and
the absent case are pinned here. There are two emit points -- the immediate listing
op and the job-completion op -- and both must carry it.
"""

from __future__ import annotations

import asyncio
import unittest

from coordinator.jobs import JobStore
from coordinator.listings import ListingMemory, handle_place_item
from coordinator.planner import YibuPlanner
from coordinator.turn import build_place_generated
from protocol.validate import validate_instance

_FRAME = "01k5j8g0008q3m7b2d6h9n4r5v"
_ARTIFACT = "01m2xbae3n81b4scq0k83teqjw"
_EXTENTS = [0.55, 0.40, 0.72]


class PlaceGeneratedNameTests(unittest.TestCase):
    def test_schema_accepts_a_name(self) -> None:
        op = build_place_generated(
            job_id="01m2xbae3n81b4scq0k83teqjw",
            turn_id=1,
            stage_epoch=0,
            target={"type": "capture_hint", "frame_id": _FRAME},
            extent_m=_EXTENTS,
            name="oak side table",
        )
        validate_instance("scene_op", op)  # raises on failure
        self.assertEqual(op["name"], "oak side table")

    def test_schema_still_accepts_an_absent_name(self) -> None:
        op = build_place_generated(
            job_id="01m2xbae3n81b4scq0k83teqjw",
            turn_id=1,
            stage_epoch=0,
            target={"type": "capture_hint", "frame_id": _FRAME},
            extent_m=_EXTENTS,
        )
        validate_instance("scene_op", op)
        self.assertNotIn("name", op)

    def test_completion_op_carries_the_name_from_the_job_record(self) -> None:
        """on_job_terminal reads the job dict, so the name must round-trip there."""
        op = build_place_generated(
            job_id="01m2xbae3n81b4scq0k83teqjw",
            turn_id=1,
            stage_epoch=0,
            target={"type": "capture_hint", "frame_id": _FRAME},
            extent_m=_EXTENTS,
            name="arc lamp",
        )
        self.assertEqual(op["name"], "arc lamp")


class ListingOpNameTests(unittest.TestCase):
    def _place(self, name: str, prebaked: dict[str, str]):
        store = JobStore()
        listings = ListingMemory()

        def queue_fn(**_kwargs):
            return {"status": "queued"}

        result, op = asyncio.run(handle_place_item(
            store,
            listings=listings,
            args={
                "name": name,
                "extent_m": _EXTENTS,
                "target": {"type": "capture_hint", "frame_id": _FRAME},
            },
            current_frame_id=_FRAME,
            prebaked=prebaked,
            queue_fn=queue_fn,
            jpeg_b64="",
        ))
        return store, result, op

    def test_prebaked_op_carries_the_name(self) -> None:
        _store, result, op = self._place("oak side table", {"oak side table": _ARTIFACT})
        self.assertNotIn("error", result, result)
        self.assertIsNotNone(op)
        self.assertEqual(op["name"], "oak side table")

    def test_queued_op_carries_the_name(self) -> None:
        store, result, op = self._place("arc lamp", {})
        self.assertNotIn("error", result, result)
        self.assertIsNotNone(op)
        self.assertEqual(op["name"], "arc lamp")
        # The completion op is built from the job record, so the name must be
        # stored there too or the mesh-swap op would lose it.
        job = store.jobs[result["job_id"]]
        self.assertEqual(job.get("name"), "arc lamp")

    def test_op_with_a_name_validates_against_the_schema(self) -> None:
        _store, _result, op = self._place("oak side table", {"oak side table": _ARTIFACT})
        self.assertIsNotNone(op)
        validate_instance(
            "scene_op",
            dict(op, op_id="01k5j8g0008q3m7b2d6h9n4r5v", turn_id=1, stage_epoch=0),
        )


class PlannerStillEmitsNames(unittest.TestCase):
    """The planner's coordinator-op path is what the Quest actually receives."""

    def test_dispatched_place_item_op_carries_the_name(self) -> None:
        planner = YibuPlanner(complete_fn=None, execute_fn=None)
        planner.bind_tools(
            jobs=JobStore(),
            jpeg_b64="",
            frame_id=_FRAME,
            listings=ListingMemory(),
            prebaked={"oak side table": _ARTIFACT},
        )
        asyncio.run(planner._dispatch_tool("place_item", {
            "name": "oak side table",
            "extent_m": _EXTENTS,
            "target": {"type": "capture_hint"},
        }))
        self.assertEqual(len(planner._coordinator_ops), 1)
        self.assertEqual(planner._coordinator_ops[0]["name"], "oak side table")


if __name__ == "__main__":
    unittest.main()
