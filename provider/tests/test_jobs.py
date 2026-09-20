from __future__ import annotations

import asyncio
import unittest

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

    def test_unknown_job_may_not_place(self) -> None:
        self.assertFalse(coordinator_may_place(self.store, new_ulid()))

    def test_clear_jobs_blocks_place(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {"job_id": job_id, "status": "queued"}
        mark_ready(self.store, job_id)
        self.assertTrue(coordinator_may_place(self.store, job_id))
        clear_jobs(self.store)
        self.assertFalse(coordinator_may_place(self.store, job_id))
