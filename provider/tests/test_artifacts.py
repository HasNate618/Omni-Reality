from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coordinator.artifacts import artifact_path, serve_artifact_bytes
from coordinator.jobs import JobStore, mark_ready
from protocol.ids import new_ulid


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_rejects_non_ulid_and_dotdot(self) -> None:
        root = Path(self.tmpdir)
        self.assertIsNone(artifact_path(root, "../etc/passwd"))
        self.assertIsNone(artifact_path(root, "not-a-ulid"))
        job_id = new_ulid()
        self.assertEqual(artifact_path(root, job_id), root / f"{job_id}.glb")

    def test_unknown_job_is_404(self) -> None:
        store = JobStore()
        job_id = new_ulid()
        self.assertIsNone(serve_artifact_bytes(store, Path(self.tmpdir), job_id))

    def test_ready_job_serves_glb(self) -> None:
        store = JobStore()
        job_id = new_ulid()
        store.jobs[job_id] = {"job_id": job_id, "status": "queued"}
        mark_ready(store, job_id)
        root = Path(self.tmpdir)
        payload = b"glTF" + b"\x00" * 8
        (root / f"{job_id}.glb").write_bytes(payload)
        self.assertEqual(serve_artifact_bytes(store, root, job_id), payload)
