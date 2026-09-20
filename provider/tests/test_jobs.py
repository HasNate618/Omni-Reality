from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from coordinator.jobs import (
    JobStore,
    clear_jobs,
    coordinator_may_place,
    handle_inspect,
    handle_start_generation,
    mark_ready,
    poll_queued_jobs,
    session_generation_busy,
)
from coordinator.server import CoordinatorState, handle_text, run_job_poller
from coordinator.turn import on_job_terminal
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


def _artifact_target() -> dict:
    return load_fixture("valid", "scene_op_mark.json")["target"]


class JobPollerScanTests(unittest.TestCase):
    """The single-scan seam: a queued job settles when its artifact lands.

    Before this scan existed, `mark_ready` and `on_job_terminal` had no
    production caller, so a live-queued job sat at `queued` forever: Quest
    retried the artifact fetch six times, gave up, and left a correct listed
    box with no mesh (plan finding #2).
    """

    def setUp(self) -> None:
        self.store = JobStore()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settled: list[str] = []

    def _queue(self, **fields: object) -> str:
        job_id = new_ulid()
        job = {"job_id": job_id, "frame_id": _artifact_target()["frame_id"],
               "target": _artifact_target(), "status": "queued"}
        job.update(fields)
        self.store.jobs[job_id] = job
        return job_id

    def _scan(self, *, exists=None, status_fn=None, on_terminal=None):
        async def terminal(job_id: str) -> None:
            self.settled.append(job_id)

        return asyncio.run(poll_queued_jobs(
            self.store,
            self.root,
            on_terminal=on_terminal or terminal,
            exists_fn=exists if exists is not None else (lambda path: False),
            status_fn=status_fn,
        ))

    def test_artifact_landing_marks_ready_and_runs_the_terminal(self) -> None:
        job_id = self._queue()
        seen: list[tuple[str, str]] = []

        async def terminal(found: str) -> None:
            seen.append((found, self.store.jobs[found]["status"]))

        settled = self._scan(
            exists=lambda path: path == self.root / f"{job_id}.glb",
            on_terminal=terminal,
        )
        self.assertEqual(settled, [job_id])
        self.assertEqual(self.store.jobs[job_id]["status"], "ready")
        self.assertTrue(coordinator_may_place(self.store, job_id),
                        "a ready job is what the artifact server will serve")
        self.assertEqual(seen, [(job_id, "ready")],
                         "the status must be ready before the terminal runs")

    def test_absent_artifact_leaves_the_job_queued(self) -> None:
        job_id = self._queue()
        settled = self._scan(status_fn=lambda *, job_id: "running")
        self.assertEqual(settled, [])
        self.assertEqual(self.settled, [])
        self.assertEqual(self.store.jobs[job_id]["status"], "queued")

    def test_worker_failure_marks_failed_and_runs_the_terminal(self) -> None:
        job_id = self._queue(extent_m=[0.55, 0.40, 0.72], planted=True)
        settled = self._scan(status_fn=lambda *, job_id: "failed")
        self.assertEqual(settled, [job_id])
        self.assertEqual(self.store.jobs[job_id]["status"], "failed")
        self.assertEqual(self.settled, [job_id])

    def test_unreachable_worker_leaves_the_job_queued(self) -> None:
        # None is "could not tell": a job is never settled on a guess, so one
        # unreachable status GET does not fail a healthy generation.
        job_id = self._queue()
        settled = self._scan(status_fn=lambda *, job_id: None)
        self.assertEqual(settled, [])
        self.assertEqual(self.store.jobs[job_id]["status"], "queued")

    def test_already_settled_jobs_are_not_rescanned(self) -> None:
        ready_id = self._queue(status="ready")
        failed_id = self._queue(status="failed")
        settled = self._scan(exists=lambda path: True)
        self.assertEqual(settled, [], "only queued jobs are candidates")
        self.assertEqual(self.settled, [])
        self.assertEqual(self.store.jobs[ready_id]["status"], "ready")
        self.assertEqual(self.store.jobs[failed_id]["status"], "failed")

    def test_malformed_job_id_is_never_settled(self) -> None:
        # Readiness pathing comes from artifacts.artifact_path's ULID guard, so
        # a job id that is not a ULID can never name an artifact on disk.
        job_id = self._queue()
        self.store.jobs["../../etc/passwd"] = {"job_id": "../../etc/passwd", "status": "queued"}
        settled = self._scan(exists=lambda path: True)
        self.assertEqual(settled, [job_id])
        self.assertEqual(self.store.jobs["../../etc/passwd"]["status"], "queued")

    def test_planted_extent_job_takes_the_no_second_op_path(self) -> None:
        # The scan must not turn a planted job into a second placement: the op
        # went out at accept and Quest is already fetching the artifact.
        job_id = self._queue(extent_m=[0.55, 0.40, 0.72], planted=True)
        sent: list[dict] = []
        announced: list[dict] = []

        async def send(op: dict) -> dict | None:
            sent.append(op)
            return {"status": "placed", "op_id": op["op_id"]}

        async def complete_final_fn(ack: dict) -> None:
            announced.append(ack)

        state = CoordinatorState(jobs=self.store)
        state.latest_stage_epoch = 1

        async def run() -> None:
            await on_job_terminal(state, job_id, send, complete_final_fn)

        settled = self._scan(
            exists=lambda path: path == self.root / f"{job_id}.glb",
            on_terminal=lambda found: run(),
        )
        self.assertEqual(settled, [job_id])
        self.assertEqual(sent, [], "already planted; no second op")
        self.assertEqual(announced, [{"status": "ready", "planted": True}])

    def test_worker_failure_reports_failure_through_the_terminal(self) -> None:
        job_id = self._queue(extent_m=[0.55, 0.40, 0.72], planted=True)
        sent: list[dict] = []
        announced: list[dict] = []
        state = CoordinatorState(jobs=self.store)
        state.latest_stage_epoch = 1

        async def send(op: dict) -> dict | None:
            sent.append(op)
            return {"status": "placed", "op_id": op["op_id"]}

        async def complete_final_fn(ack: dict) -> None:
            announced.append(ack)

        async def run(found: str) -> None:
            await on_job_terminal(state, found, send, complete_final_fn)

        settled = self._scan(status_fn=lambda *, job_id: "failed", on_terminal=run)
        self.assertEqual(settled, [job_id])
        self.assertEqual(sent, [], "a failed job emits no op")
        self.assertEqual(announced, [{"status": "failed"}],
                         "the box stays; the mesh never arrived")


class _PollerWs:
    """Fake socket: records frames and ACKs one scene_op like Quest would."""

    def __init__(self, state: CoordinatorState, *, ack: bool = True) -> None:
        self.state = state
        self.ack = ack
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        if message["type"] != "scene_op" or not self.ack:
            return
        payload = copy.deepcopy(load_fixture("valid", "placement_ack_placed.json"))
        payload["op_id"] = message["payload"]["op_id"]
        payload["turn_id"] = message["turn_id"]
        payload["stage_epoch"] = message["payload"]["stage_epoch"]
        await handle_text(self, self.state, json.dumps({
            "v": 1, "type": "ack", "session_id": None,
            "turn_id": message["turn_id"], "utterance_id": None, "payload": payload,
        }))

    def ops(self) -> list[dict]:
        return [m["payload"] for m in self.sent if m["type"] == "scene_op"]


class JobPollerLoopTests(unittest.TestCase):
    """server.run_job_poller wiring: artifact on disk → job settled."""

    def setUp(self) -> None:
        self.store = JobStore()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = CoordinatorState(jobs=self.store)
        self.state.artifact_root = self.root
        self.state.latest_stage_epoch = 1

    def _queue(self, job_id: str, **fields: object) -> None:
        job = {"job_id": job_id, "frame_id": _artifact_target()["frame_id"],
               "target": _artifact_target(), "status": "queued"}
        job.update(fields)
        self.store.jobs[job_id] = job

    def _write_artifact(self, job_id: str) -> None:
        (self.root / f"{job_id}.glb").write_bytes(b"glTF" + b"\x00" * 8)

    def _run_poller(self, ws: _PollerWs, predicate, *, status_fn=None) -> None:
        async def scenario() -> bool:
            task = asyncio.create_task(run_job_poller(
                ws, self.state, interval_s=0.01, status_fn=status_fn))
            try:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if predicate():
                        return True
                    await asyncio.sleep(0.01)
                return False
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(asyncio.run(scenario()), "poller never settled the job")

    def test_planted_job_with_artifact_is_ready_and_emits_no_op(self) -> None:
        job_id = new_ulid()
        self._queue(job_id, extent_m=[0.55, 0.40, 0.72], planted=True)
        self._write_artifact(job_id)
        ws = _PollerWs(self.state)

        with self.assertLogs("coordinator.server", level="INFO") as captured:
            self._run_poller(
                ws,
                lambda: self.store.jobs[job_id]["status"] == "ready"
                and any("job settled status=ready planted=True" in line for line in captured.output),
            )

        self.assertEqual(ws.ops(), [], "the box was planted at accept; no second op")
        self.assertEqual(ws.sent, [], "no frame at all is needed for a planted job")

    def test_ready_artifact_settles_an_extentless_job_as_a_failure(self) -> None:
        # The poller settles this job in production now, and a job queued by
        # `start_generation` states no size. Quest refuses an extentless
        # `place_generated` rather than inventing one (spec §6.3), so the
        # coordinator emits nothing and settles the turn as failed. This test
        # used to assert the pre-refusal contract -- one extentless op ACKed
        # "placed" -- which is exactly the op I5 made unplantable.
        job_id = new_ulid()
        self._queue(job_id)
        self._write_artifact(job_id)
        ws = _PollerWs(self.state)

        with self.assertLogs("coordinator.server", level="INFO") as captured:
            self._run_poller(
                ws,
                lambda: self.store.jobs[job_id]["status"] == "ready"
                and any("job settled status=failed" in line for line in captured.output),
            )

        self.assertEqual(ws.ops(), [], "an op Quest would refuse is not sent")
        self.assertEqual(ws.sent, [], "no frame at all: there is nothing to plant")
        self.assertEqual(self.state.completed_ops, {}, "no ACK can be waited for")

        # A later scan must not settle it again: the job left `queued` at ready.
        async def terminal(job_id: str) -> None:
            raise AssertionError("a settled job must not be scanned again")

        self.assertEqual(asyncio.run(poll_queued_jobs(
            self.store, self.root, on_terminal=terminal,
            exists_fn=lambda path: True)), [])

    def test_worker_failure_settles_the_job_without_sending(self) -> None:
        job_id = new_ulid()
        self._queue(job_id, extent_m=[0.55, 0.40, 0.72], planted=True)
        ws = _PollerWs(self.state)

        self._run_poller(
            ws,
            lambda: self.store.jobs[job_id]["status"] == "failed",
            status_fn=lambda *, job_id: "failed",
        )

        self.assertEqual(ws.ops(), [])
        self.assertFalse(coordinator_may_place(self.store, job_id))
        self.assertFalse(
            session_generation_busy(self.store),
            "a failed job must not leave the session refusing every later placement",
        )
