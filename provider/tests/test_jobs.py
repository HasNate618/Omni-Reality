from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from coordinator.jobs import (
    JobStore,
    clear_jobs,
    coordinator_may_place,
    handle_inspect,
    handle_start_generation,
    mark_ready,
)
from protocol.ids import new_ulid
from protocol.validate import load_fixture, validate_instance


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = JobStore()
        self.frame_id = load_fixture("valid", "capture_envelope.json")["frame_id"]
        self.target = {
            "type": "image_box",
            "frame_id": self.frame_id,
            "u0": 0.2, "v0": 0.1, "u1": 0.8, "v1": 0.9,
        }
        self.jpeg = "qq=="

    def test_inspect_stamps_object_id_and_strips_mask(self) -> None:
        async def inspect_fn(**kwargs):
            return {"objects": [{
                "u0": 0.31, "v0": 0.22, "u1": 0.74, "v1": 0.81,
                "score": 0.91, "mask_png_b64": "MASK",
            }]}

        result = asyncio.run(handle_inspect(
            self.store,
            frame_id=self.frame_id,
            jpeg_b64=self.jpeg,
            target=self.target,
            phrase="chair",
            current_frame_id=self.frame_id,
            inspect_fn=inspect_fn,
        ))
        validate_instance("inspect_objects_result", result)
        self.assertEqual(result["count"], 1)
        oid = result["objects"][0]["object_id"]
        self.assertNotIn("mask_png_b64", result["objects"][0])
        self.assertEqual(self.store.objects[oid]["mask_png_b64"], "MASK")

    def test_inspect_foreign_frame_is_error_not_boxes(self) -> None:
        result = asyncio.run(handle_inspect(
            self.store,
            frame_id=new_ulid(),
            jpeg_b64=self.jpeg,
            target=self.target,
            phrase=None,
            current_frame_id=self.frame_id,
            inspect_fn=lambda **k: (_ for _ in ()).throw(AssertionError("must not call worker")),
        ))
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["objects"], [])
        self.assertEqual(result["error"], "invalid")

    def test_inspect_timeout_error(self) -> None:
        async def inspect_fn(**kwargs):
            raise TimeoutError("sam2")

        result = asyncio.run(handle_inspect(
            self.store, frame_id=self.frame_id, jpeg_b64=self.jpeg,
            target=self.target, phrase=None, current_frame_id=self.frame_id,
            inspect_fn=inspect_fn,
        ))
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["count"], 0)

    def test_start_generation_queues_and_busy(self) -> None:
        asyncio.run(handle_inspect(
            self.store, frame_id=self.frame_id, jpeg_b64=self.jpeg,
            target=self.target, phrase=None, current_frame_id=self.frame_id,
            inspect_fn=lambda **k: {"objects": [{"u0": 0.3, "v0": 0.2, "u1": 0.7, "v1": 0.8, "score": 0.9}]},
        ))
        oid = next(iter(self.store.objects))
        queued = []

        async def queue_fn(**kwargs):
            queued.append(kwargs)
            return {"status": "queued"}

        result = asyncio.run(handle_start_generation(
            self.store, args={"object_id": oid, "prompt": "chair"},
            current_frame_id=self.frame_id, jpeg_b64=self.jpeg, queue_fn=queue_fn,
        ))
        validate_instance("start_generation_result", result)
        self.assertEqual(result["status"], "queued")
        busy = asyncio.run(handle_start_generation(
            self.store, args={"object_id": oid},
            current_frame_id=self.frame_id, jpeg_b64=self.jpeg, queue_fn=queue_fn,
        ))
        self.assertEqual(busy["error"], "busy")
        self.assertEqual(len(queued), 1)

    def test_queue_fn_failure_strands_no_job(self) -> None:
        # A non-BusyError failure from the worker (httpx transport error, or
        # queue_job's RuntimeError("queue_job non-object")) must not leave the
        # job behind as queued: that makes session_generation_busy true for
        # the rest of the session, refusing every later generation and
        # placement. The failure must propagate, not be swallowed.
        asyncio.run(handle_inspect(
            self.store, frame_id=self.frame_id, jpeg_b64=self.jpeg,
            target=self.target, phrase=None, current_frame_id=self.frame_id,
            inspect_fn=lambda **k: {"objects": [{"u0": 0.3, "v0": 0.2, "u1": 0.7, "v1": 0.8, "score": 0.9}]},
        ))
        oid = next(iter(self.store.objects))

        async def queue_fn(**kwargs):
            raise RuntimeError("queue_job non-object")

        with self.assertRaises(RuntimeError):
            asyncio.run(handle_start_generation(
                self.store, args={"object_id": oid, "prompt": "chair"},
                current_frame_id=self.frame_id, jpeg_b64=self.jpeg, queue_fn=queue_fn,
            ))
        self.assertEqual(self.store.jobs, {}, "no job may survive a worker failure")

    def test_unknown_job_may_not_place(self) -> None:
        self.assertFalse(coordinator_may_place(self.store, new_ulid()))

    def test_clear_jobs_blocks_place(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {"job_id": job_id, "status": "queued"}
        mark_ready(self.store, job_id)
        self.assertTrue(coordinator_may_place(self.store, job_id))
        clear_jobs(self.store)
        self.assertFalse(coordinator_may_place(self.store, job_id))

    def test_clear_jobs_keeps_prebaked_artifacts(self) -> None:
        # The pre-baked GLB is the operator's night-before bake, not the
        # session's. Unlinking it on teardown made the pre-bake single-use:
        # take two got a 404, six retries, and a box with no mesh (spec §5.3).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prebaked_id = new_ulid()
            owned_id = new_ulid()
            self.store.jobs[prebaked_id] = {
                "job_id": prebaked_id, "status": "ready", "prebaked": True,
            }
            self.store.jobs[owned_id] = {"job_id": owned_id, "status": "ready"}
            for job_id in (prebaked_id, owned_id):
                (root / f"{job_id}.glb").write_bytes(b"glTF" + b"\x00" * 8)

            clear_jobs(self.store, root)

            self.assertTrue(
                (root / f"{prebaked_id}.glb").is_file(),
                "a pre-baked artifact must survive session teardown")
            self.assertFalse(
                (root / f"{owned_id}.glb").exists(),
                "a session-owned artifact is still reclaimed")
