from __future__ import annotations

import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from coordinator.artifacts import artifact_path, serve_artifact_bytes, start_artifact_server
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

    def test_bound_server_serves_ready_glb_and_404s_unknown(self) -> None:
        # start_artifact_server was never called anywhere, so nothing listened
        # on 8766 and Quest's bounded fetch 404'd on every path, pre-baked
        # included. Bind an ephemeral port and fetch end to end.
        store = JobStore()
        job_id = new_ulid()
        store.jobs[job_id] = {"job_id": job_id, "status": "queued"}
        mark_ready(store, job_id)
        root = Path(self.tmpdir)
        payload = b"glTF" + b"\x00" * 8
        (root / f"{job_id}.glb").write_bytes(payload)

        server = start_artifact_server("127.0.0.1", 0, store, root)
        try:
            port = server.server_address[1]
            base = f"http://127.0.0.1:{port}/artifacts/"
            with urllib.request.urlopen(base + job_id + ".glb", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read(), payload)
            unknown = new_ulid()
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(base + unknown + ".glb", timeout=5)
            self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
