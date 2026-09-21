from __future__ import annotations

import asyncio
import unittest

import httpx

from coordinator.jobs import JobStore, session_generation_busy
from coordinator.listings import ListingMemory, handle_place_item, normalize_name


class NormalizeNameTests(unittest.TestCase):
    def test_folds_case_and_collapses_space(self) -> None:
        self.assertEqual(normalize_name("  Oak   Side Table "), "oak side table")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(normalize_name(""), "")


class ListingMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = ListingMemory()

    def test_record_then_get(self) -> None:
        self.memory.record(
            "oak side table", [0.55, 0.40, 0.72], "01k5j8g0008q3m7b2d6h9n4r5v", "page"
        )
        row = self.memory.get("Oak Side Table")
        self.assertIsNotNone(row)
        self.assertEqual(row["extent_m"], [0.55, 0.40, 0.72])
        self.assertEqual(row["source"], "page")
        self.assertEqual(row["source_frame_id"], "01k5j8g0008q3m7b2d6h9n4r5v")

    def test_same_name_updates_one_row(self) -> None:
        self.memory.record("oak side table", [0.55, 0.40, 0.72], "f1", "page")
        self.memory.record("Oak Side Table", [0.60, 0.40, 0.72], "f2", "spoken")
        self.assertEqual(len(self.memory.rows()), 1)
        self.assertEqual(self.memory.get("oak side table")["extent_m"], [0.60, 0.40, 0.72])
        self.assertEqual(self.memory.get("oak side table")["source"], "spoken")

    def test_distinct_names_keep_distinct_rows(self) -> None:
        self.memory.record("table", [0.55, 0.40, 0.72], "f1", "page")
        self.memory.record("lamp", [0.30, 0.30, 1.50], "f1", "page")
        self.assertEqual(len(self.memory.rows()), 2)

    def test_unknown_name_is_none(self) -> None:
        self.assertIsNone(self.memory.get("nothing here"))

    def test_clear_empties(self) -> None:
        self.memory.record("table", [0.55, 0.40, 0.72], "f1", "page")
        self.memory.clear()
        self.assertEqual(self.memory.rows(), [])

    def test_stores_copies_not_caller_list(self) -> None:
        extents = [0.55, 0.40, 0.72]
        self.memory.record("table", extents, "f1", "page")
        extents[0] = 9.99
        self.assertEqual(self.memory.get("table")["extent_m"], [0.55, 0.40, 0.72])


_FRAME = "01k5j8g0008q3m7b2d6h9n4r5v"
_TARGET = {"type": "capture_hint", "frame_id": _FRAME}
_ARTIFACT = "01m2xbae3n81b4scq0k83teqjw"


class HandlePlaceItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = JobStore()
        self.memory = ListingMemory()

    def _call(self, args, *, prebaked=None, current_frame_id=_FRAME, jpeg_b64=None,
              queue_fn=None):
        return asyncio.run(handle_place_item(
            self.store,
            listings=self.memory,
            args=args,
            current_frame_id=current_frame_id,
            prebaked=prebaked or {},
            jpeg_b64=jpeg_b64,
            queue_fn=queue_fn,
        ))

    def _good(self) -> dict:
        return {"name": "oak side table", "extent_m": [0.55, 0.40, 0.72], "target": _TARGET}

    def test_valid_args_return_an_op(self) -> None:
        result, op = self._call(self._good())
        self.assertNotIn("error", result)
        self.assertEqual(op["kind"], "place_generated")
        self.assertEqual(op["extent_m"], [0.55, 0.40, 0.72])
        self.assertEqual(op["target"], _TARGET)

    def test_valid_args_record_a_row(self) -> None:
        self._call(self._good())
        row = self.memory.get("oak side table")
        self.assertEqual(row["extent_m"], [0.55, 0.40, 0.72])
        self.assertEqual(row["source_frame_id"], _FRAME)

    def test_sizes_with_no_image_are_recorded_as_spoken(self) -> None:
        # No camera image arrived, so the model cannot have read these sizes off
        # a listing. Labelling the row "page" here is exactly what the honesty
        # rubric forbids.
        self._call(self._good())
        self.assertEqual(self.memory.get("oak side table")["source"], "spoken")

    def test_sizes_with_an_image_are_recorded_as_page(self) -> None:
        self._call(self._good(), jpeg_b64="qq==")
        self.assertEqual(self.memory.get("oak side table")["source"], "page")

    def test_unreachable_worker_still_plants_the_box(self) -> None:
        # demo.md's fallback is "mesh fails -> boxes carry the demo", so a dead
        # worker must not abort the turn: the box is sized from the listing and
        # never waits on the mesh. It used to raise out of the tool loop, which
        # spoke "Sorry, I couldn't reach the model" and planted nothing.
        async def dead_worker(**kwargs):
            raise httpx.ConnectError("worker down")

        result, op = self._call(self._good(), jpeg_b64="qq==", queue_fn=dead_worker)

        self.assertEqual(result["error"], "worker_unavailable")
        self.assertEqual(result["listed"], "oak side table")
        self.assertIsNotNone(op, "the true-scale box must still be emitted")
        self.assertEqual(op["kind"], "place_generated")
        self.assertEqual(self.memory.get("oak side table")["source"], "page")
        # The job must not stay queued: a queued job keeps
        # session_generation_busy true and refuses every later placement.
        self.assertEqual(self.store.jobs[op["job_id"]]["status"], "failed")
        self.assertFalse(session_generation_busy(self.store))

    def test_worker_http_status_error_also_plants_the_box(self) -> None:
        # A 500 is the same story as an unreachable worker: no mesh, but the
        # placement is still true.
        async def failing_worker(**kwargs):
            raise httpx.ConnectError("refused")

        result, op = self._call(self._good(), queue_fn=failing_worker)
        self.assertEqual(result["error"], "worker_unavailable")
        self.assertIsNotNone(op)

    def test_worker_refusal_is_still_a_refusal(self) -> None:
        # A busy worker is NOT a transport failure: nothing was queued, so no
        # placement happened and no row may be recorded.
        from workers.gen_client import BusyError

        async def busy_worker(**kwargs):
            raise BusyError("busy")

        result, op = self._call(self._good(), queue_fn=busy_worker)
        self.assertEqual(result["error"], "busy")
        self.assertIsNone(op)
        self.assertEqual(self.memory.rows(), [])

    def test_missing_extent_m_refused(self) -> None:
        result, op = self._call({"name": "table", "target": _TARGET})
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)
        self.assertEqual(self.memory.rows(), [])

    def test_wrong_axis_count_refused(self) -> None:
        args = self._good()
        args["extent_m"] = [0.55, 0.40]
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_out_of_range_axis_refused(self) -> None:
        for bad in (0.04, 3.01, -0.5):
            args = self._good()
            args["extent_m"] = [bad, 0.40, 0.72]
            result, op = self._call(args)
            self.assertEqual(result["error"], "invalid", f"axis {bad} should refuse")
            self.assertIsNone(op)

    def test_non_numeric_axis_refused(self) -> None:
        args = self._good()
        args["extent_m"] = [0.55, 0.40, "tall"]
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_queue_kwargs_match_the_real_worker_signature(self) -> None:
        # Regression guard for C1: every fake queue_fn in this file was a
        # **kwargs stub, which accepted an `extent_m` kwarg and hid the
        # TypeError that killed every non-pre-baked placement before any HTTP
        # request. The sent kwargs must be a subset of the real signature.
        import inspect

        from workers.gen_client import queue_job

        sent: dict = {}

        async def queue_fn(**kwargs):
            sent.update(kwargs)
            return {"status": "queued"}

        asyncio.run(handle_place_item(
            self.store, listings=self.memory, args=self._good(),
            current_frame_id=_FRAME, prebaked={}, queue_fn=queue_fn,
            jpeg_b64="qq==",
        ))
        accepted = set(inspect.signature(queue_job).parameters)
        self.assertTrue(
            set(sent) <= accepted, f"unexpected kwargs: {set(sent) - accepted}")
        # The worker is image→3D only; a None jpeg would leave it nothing to
        # generate from. handle_place_item carries the turn's frame JPEG here.
        self.assertEqual(sent["jpeg_b64"], "qq==")

    def test_empty_name_refused(self) -> None:
        args = self._good()
        args["name"] = "   "
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_overlong_name_refused(self) -> None:
        args = self._good()
        args["name"] = "x" * 41
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_no_current_frame_refused(self) -> None:
        result, op = self._call(self._good(), current_frame_id=None)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_stale_frame_target_refused(self) -> None:
        args = self._good()
        args["target"] = {"type": "capture_hint", "frame_id": "01k5j8g0008q3m7b2d6h9n4r5w"}
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_world_point_target_refused(self) -> None:
        args = self._good()
        args["target"] = {"type": "world_point", "px": 1.0, "py": 1.0, "pz": 1.0,
                          "frame": "openxr_floor_stage"}
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_live_path_queues_a_job(self) -> None:
        result, op = self._call(self._good())
        job = self.store.jobs[op["job_id"]]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["extent_m"], [0.55, 0.40, 0.72])
        self.assertTrue(job["planted"])

    def test_prebaked_path_creates_a_ready_job_with_no_worker(self) -> None:
        queued = []

        async def queue_fn(**kwargs):
            queued.append(kwargs)
            return {"status": "queued"}

        result, op = asyncio.run(handle_place_item(
            self.store,
            listings=self.memory,
            args=self._good(),
            current_frame_id=_FRAME,
            prebaked={"oak side table": _ARTIFACT},
            queue_fn=queue_fn,
        ))
        self.assertNotIn("error", result)
        self.assertEqual(op["job_id"], _ARTIFACT)
        self.assertEqual(self.store.jobs[_ARTIFACT]["status"], "ready")
        self.assertTrue(self.store.jobs[_ARTIFACT]["planted"])
        self.assertEqual(queued, [])

    def test_repeat_placement_updates_one_row(self) -> None:
        self._call(self._good())
        args = self._good()
        args["extent_m"] = [0.60, 0.40, 0.72]
        self._call(args)
        self.assertEqual(len(self.memory.rows()), 1)
        self.assertEqual(self.memory.get("oak side table")["extent_m"], [0.60, 0.40, 0.72])

    def test_first_item_sits_at_the_hit(self) -> None:
        _, op = self._call(self._good())
        self.assertAlmostEqual(op["offset_m"], 0.0, places=6)

    def test_second_item_clears_the_first(self) -> None:
        self._call(self._good())
        lamp = {"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET}
        _, op = self._call(lamp)
        # first width 0.55 half + 0.05 gap + second width 0.30 half
        self.assertAlmostEqual(op["offset_m"], 0.275 + 0.05 + 0.15, places=6)

    def test_repeat_placement_keeps_its_slot(self) -> None:
        self._call(self._good())
        self._call({"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET})
        _, op = self._call(self._good())
        self.assertAlmostEqual(op["offset_m"], 0.0, places=6)

    def test_result_reports_the_packed_run_length(self) -> None:
        self._call(self._good())
        lamp = {"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET}
        result, _ = self._call(lamp)
        self.assertAlmostEqual(result["run_length_m"], 0.55 + 0.30 + 0.05, places=6)

    def test_run_length_present_on_the_prebaked_path_too(self) -> None:
        result, _ = asyncio.run(handle_place_item(
            self.store,
            listings=self.memory,
            args=self._good(),
            current_frame_id=_FRAME,
            prebaked={"oak side table": _ARTIFACT},
        ))
        self.assertAlmostEqual(result["run_length_m"], 0.55, places=6)

    def test_boolean_axis_is_refused(self) -> None:
        # isinstance(True, int) is True in Python, so a bool must be rejected
        # explicitly rather than slipping through the numeric check.
        args = self._good()
        args["extent_m"] = [True, 0.40, 0.72]
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)
        self.assertEqual(self.memory.rows(), [])

    def test_busy_refusal_leaves_no_trace(self) -> None:
        # A worker is queued for the first item, so the second is refused.
        # A refusal must not record a row or queue a worker.
        calls = []

        async def queue_fn(**kwargs):
            calls.append(kwargs)
            return {"status": "queued"}

        first, _ = asyncio.run(handle_place_item(
            self.store, listings=self.memory, args=self._good(),
            current_frame_id=_FRAME, prebaked={}, queue_fn=queue_fn))
        self.assertNotIn("error", first)
        rows_after_first = len(self.memory.rows())

        lamp = {"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET}
        result, op = asyncio.run(handle_place_item(
            self.store, listings=self.memory, args=lamp,
            current_frame_id=_FRAME, prebaked={}, queue_fn=queue_fn))
        self.assertEqual(result["error"], "busy")
        self.assertIsNone(op)
        self.assertEqual(
            len(self.memory.rows()), rows_after_first,
            "a refused placement must not record a row")
        self.assertEqual(len(calls), 1, "a refused placement must not queue a worker")

    def test_no_worker_bound_means_no_busy_check(self) -> None:
        # With no worker there is nothing to serialize: the pre-baked and
        # demo paths place several items back to back.
        self._call(self._good())
        lamp = {"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET}
        result, op = self._call(lamp)
        self.assertNotIn("error", result)
        self.assertIsNotNone(op)

    def test_worker_busy_error_leaves_no_trace(self) -> None:
        # The pre-check passes here (nothing is queued yet) and the worker
        # itself refuses with BusyError. This is the path that a test driving
        # only the pre-check cannot reach, and it is the one that used to
        # leave a recorded row behind.
        from workers.gen_client import BusyError

        async def queue_fn(**kwargs):
            raise BusyError("worker busy")

        result, op = asyncio.run(handle_place_item(
            self.store, listings=self.memory, args=self._good(),
            current_frame_id=_FRAME, prebaked={}, queue_fn=queue_fn))
        self.assertEqual(result["error"], "busy")
        self.assertIsNone(op)
        self.assertEqual(self.memory.rows(), [], "no row may be recorded")
        self.assertEqual(self.store.jobs, {}, "no job may survive a refusal")

    def test_raise_inside_the_guarded_region_strands_no_job(self) -> None:
        # A job left behind as queued makes session_generation_busy true for
        # the rest of the session, refusing every later placement. Anything
        # raised between job creation and the result must pop the job.
        class _RaisingRecordListings(ListingMemory):
            def record(self, name, extent_m, source_frame_id, source):
                raise RuntimeError("record blew up")

        with self.assertRaises(RuntimeError):
            asyncio.run(handle_place_item(
                self.store,
                listings=_RaisingRecordListings(),
                args=self._good(),
                current_frame_id=_FRAME,
                prebaked={},
            ))
        self.assertEqual(self.store.jobs, {}, "a stranded job would wedge the session")
