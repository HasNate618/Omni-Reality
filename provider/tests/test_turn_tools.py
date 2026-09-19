from __future__ import annotations

import asyncio
import unittest

from coordinator.jobs import JobStore, coordinator_may_place, mark_failed, mark_ready
from coordinator.session import CoordinatorState
from coordinator.turn import accept_model_ops, build_place_generated, may_speak, on_job_terminal
from protocol.ids import new_ulid
from protocol.validate import load_fixture, validate_instance


class AcceptOpsTests(unittest.TestCase):
    def test_drops_place_generated_and_caps(self) -> None:
        target = load_fixture("valid", "scene_op_mark.json")["target"]
        ghost = {
            "kind": "ghost",
            "target": target,
            "motion": {"kind": "rotate", "axis": "y", "angle_deg": 30, "period_s": 2},
        }
        ops = accept_model_ops([
            {"kind": "place_generated", "target": target, "job_id": new_ulid()},
            ghost, ghost, ghost, ghost,
        ])
        self.assertEqual(len(ops), 3)
        self.assertTrue(all(op["kind"] == "ghost" for op in ops))


class SpeakGateTests(unittest.TestCase):
    def test_no_speak_on_reject_stale_or_timeout(self) -> None:
        self.assertFalse(may_speak([{"status": "rejected"}], False))
        self.assertFalse(may_speak([{"status": "stale"}], False))
        self.assertFalse(may_speak(None, True))
        self.assertTrue(may_speak([], False))
        self.assertTrue(may_speak([{"status": "placed"}], False))


class PlaceGeneratedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = load_fixture("valid", "scene_op_mark.json")["target"]
        self.store = JobStore()
        self.state = CoordinatorState()
        self.state.jobs = self.store
        self.state.latest_stage_epoch = 1
        self.sent = []
        self.announced = []

    def test_unknown_job_place_generated_schema_valid_but_may_not_place(self) -> None:
        op = build_place_generated(
            job_id=new_ulid(), turn_id=2, stage_epoch=1, target=self.target,
        )
        validate_instance("scene_op", op)
        self.assertFalse(coordinator_may_place(self.store, op["job_id"]))

    def test_ready_job_emits_place_generated_then_announce(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "queued",
            "stage_epoch": 1,
        }
        mark_ready(self.store, job_id)

        async def send(op):
            self.sent.append(op)
            return {"status": "placed", "op_id": op["op_id"]}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["kind"], "place_generated")
        self.assertEqual(self.sent[0]["job_id"], job_id)
        self.assertEqual(self.announced[0]["status"], "placed")

    def test_failed_job_does_not_emit_place_generated(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "queued",
            "stage_epoch": 1,
        }
        mark_failed(self.store, job_id)

        async def send(op):
            self.sent.append(op)
            return {"status": "placed"}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.announced[0]["status"], "failed")

    def test_stale_epoch_is_superseded(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "ready",
            "stage_epoch": 1,
        }
        self.state.latest_stage_epoch = 2

        async def send(op):
            self.sent.append(op)
            return {"status": "placed"}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.announced, [])
