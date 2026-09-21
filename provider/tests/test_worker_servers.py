"""Unit tests for the loopback worker services.

Only pure logic is covered here. The GPU paths (real SAM2/TripoSR inference) are
exercised by driving the running services; these tests must pass on a machine
with no CUDA and no torch.
"""

from __future__ import annotations

import base64
import io
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from workers.gen_server import (
    MAX_MASK_COVERAGE,
    MIN_MASK_COVERAGE,
    WORKER_FAILED,
    WORKER_READY,
    GenerationWorker,
    decode_b64,
    mask_coverage_reason,
)
from workers.sam2_server import clamp01, target_to_clicks

try:
    import numpy  # noqa: F401

    HAVE_NUMPY = True
except Exception:  # noqa: BLE001
    HAVE_NUMPY = False


class DecodeB64Tests(unittest.TestCase):
    def test_decodes_valid_base64(self) -> None:
        self.assertEqual(decode_b64(base64.b64encode(b"hello").decode(), limit=64), b"hello")

    def test_empty_and_none_are_none(self) -> None:
        self.assertIsNone(decode_b64("", limit=64))
        self.assertIsNone(decode_b64(None, limit=64))

    def test_malformed_base64_is_none(self) -> None:
        self.assertIsNone(decode_b64("not base64!!!", limit=64))

    def test_oversized_payload_is_rejected(self) -> None:
        payload = base64.b64encode(b"x" * 500).decode()
        self.assertIsNone(decode_b64(payload, limit=10))


class MaskCoverageReasonTests(unittest.TestCase):
    def test_below_band_is_a_missed_click(self) -> None:
        self.assertEqual(mask_coverage_reason(MIN_MASK_COVERAGE / 2), "click_missed")

    def test_above_band_is_the_whole_frame(self) -> None:
        self.assertEqual(mask_coverage_reason(MAX_MASK_COVERAGE * 2), "click_whole_frame")

    def test_in_band_is_accepted(self) -> None:
        self.assertIsNone(mask_coverage_reason(0.2))


class Clamp01Tests(unittest.TestCase):
    def test_clamps_out_of_range(self) -> None:
        self.assertEqual(clamp01(1.7), 1.0)
        self.assertEqual(clamp01(-3), 0.0)

    def test_passes_through_in_range(self) -> None:
        self.assertEqual(clamp01(0.25), 0.25)

    def test_rejects_non_numbers_and_bools(self) -> None:
        self.assertIsNone(clamp01("0.5"))
        self.assertIsNone(clamp01(None))
        self.assertIsNone(clamp01(True))
        self.assertIsNone(clamp01(float("nan")))


class TargetToClicksTests(unittest.TestCase):
    def test_capture_hint_uses_the_centre(self) -> None:
        clicks = target_to_clicks({"type": "capture_hint"}, 640, 480)
        self.assertEqual(clicks, [{"x": 320, "y": 240, "label": 1, "obj_id": 1}])

    def test_image_point_maps_normalised_coords(self) -> None:
        clicks = target_to_clicks({"type": "image_point", "u": 0.5, "v": 0.5}, 640, 480)
        self.assertEqual(clicks[0]["x"], 319)
        self.assertEqual(clicks[0]["y"], 239)

    def test_image_point_out_of_range_is_clamped(self) -> None:
        clicks = target_to_clicks({"type": "image_point", "u": 9.0, "v": -2.0}, 640, 480)
        self.assertEqual(clicks[0]["x"], 639)
        self.assertEqual(clicks[0]["y"], 0)

    def test_image_box_uses_the_box_centre(self) -> None:
        clicks = target_to_clicks(
            {"type": "image_box", "u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0}, 100, 100
        )
        self.assertEqual(clicks[0]["x"], 49)
        self.assertEqual(clicks[0]["y"], 49)

    def test_pointing_without_coords_falls_back_to_centre(self) -> None:
        clicks = target_to_clicks({"type": "pointing"}, 640, 480)
        self.assertEqual(clicks, [{"x": 320, "y": 240, "label": 1, "obj_id": 1}])

    def test_non_dict_target_falls_back_to_centre(self) -> None:
        self.assertEqual(target_to_clicks(None, 10, 10), [{"x": 5, "y": 5, "label": 1, "obj_id": 1}])


@unittest.skipUnless(HAVE_NUMPY, "numpy is not installed in this venv")
class MaskGeometryTests(unittest.TestCase):
    def test_bbox_is_normalised(self) -> None:
        import numpy as np

        from workers.sam2_server import mask_bbox_norm

        mask = np.zeros((100, 200), dtype=bool)
        mask[25:75, 50:150] = True
        u0, v0, u1, v1 = mask_bbox_norm(mask)
        self.assertAlmostEqual(u0, 0.25)
        self.assertAlmostEqual(v0, 0.25)
        self.assertAlmostEqual(u1, 0.75)
        self.assertAlmostEqual(v1, 0.75)

    def test_empty_mask_has_no_bbox(self) -> None:
        import numpy as np

        from workers.sam2_server import mask_bbox_norm

        self.assertIsNone(mask_bbox_norm(np.zeros((10, 10), dtype=bool)))

    def test_encode_then_decode_round_trips(self) -> None:
        import numpy as np

        from workers.gen_server import mask_from_png
        from workers.sam2_server import encode_mask_png

        mask = np.zeros((16, 16), dtype=bool)
        mask[4:12, 4:12] = True
        decoded = mask_from_png(base64.b64decode(encode_mask_png(mask)))
        self.assertTrue((decoded == mask).all())


class _FakeMesh:
    """Writes GLB magic so the worker's publish check behaves like the real thing."""

    def __init__(self) -> None:
        self.vertices = list(range(7))

    def export(self, path, file_type=None) -> None:
        Path(path).write_bytes(b"glTF" + b"\x00" * 12)


class _FakePipeline:
    def __init__(self, *, fail_with: Exception | None = None, delay_s: float = 0.0) -> None:
        self.fail_with = fail_with
        self.delay_s = delay_s
        self.calls = 0

    def generate(self, jpeg, mask_png):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail_with is not None:
            raise self.fail_with
        return _FakeMesh(), {"sam_ms": 1.0, "tripo_ms": 2.0, "verts": 7.0}


def _wait(worker: GenerationWorker, job_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = worker.status(job_id)
        if status and status.get("status") in (WORKER_READY, WORKER_FAILED):
            return status
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never settled")


def _job_body(job_id: str, **overrides) -> dict:
    body = {
        "job_id": job_id,
        "frame_id": "01k5j8g0008q3m7b2d6h9n4r5v",
        "jpeg_b64": base64.b64encode(b"fake-jpeg").decode(),
    }
    body.update(overrides)
    return body


class GenerationWorkerTests(unittest.TestCase):
    def _worker(self, pipeline=None, root=None):
        return GenerationWorker(root or Path(self._tmp.name), pipeline or _FakePipeline())

    def setUp(self) -> None:
        # A job abandoned mid-flight (the timeout test) may still be writing when
        # the test ends, so cleanup must not fail the run.
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_success_writes_artifact_and_reports_timings(self) -> None:
        worker = self._worker()
        status, _ = worker.submit(_job_body("01k5j8g0008q3m7b2d6h9n4r5v"))
        self.assertEqual(status, 200)
        settled = _wait(worker, "01k5j8g0008q3m7b2d6h9n4r5v")
        self.assertEqual(settled["status"], WORKER_READY)
        self.assertIn("total_ms", settled["timings"])
        artifact = Path(self._tmp.name) / "01k5j8g0008q3m7b2d6h9n4r5v.glb"
        self.assertTrue(artifact.is_file())
        self.assertTrue(artifact.read_bytes().startswith(b"glTF"))

    def test_gpu_slot_is_released_after_a_successful_job(self) -> None:
        """A ready job must not leave the worker permanently busy."""
        worker = self._worker()
        first = "01k5j8g0008q3m7b2d6h9n4r5v"
        worker.submit(_job_body(first))
        _wait(worker, first)
        second = "01k5j8g0008q3m7b2d6h9n4r5w"
        status, _ = worker.submit(_job_body(second))
        self.assertEqual(status, 200, "worker still busy after a successful job")

    def test_concurrent_job_is_refused_with_409(self) -> None:
        worker = self._worker(_FakePipeline(delay_s=0.4))
        self.assertEqual(worker.submit(_job_body("01k5j8g0008q3m7b2d6h9n4r5v"))[0], 200)
        status, payload = worker.submit(_job_body("01k5j8g0008q3m7b2d6h9n4r5w"))
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "busy")

    def test_duplicate_job_id_is_refused(self) -> None:
        worker = self._worker()
        job_id = "01k5j8g0008q3m7b2d6h9n4r5v"
        worker.submit(_job_body(job_id))
        _wait(worker, job_id)
        status, payload = worker.submit(_job_body(job_id))
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "duplicate_job")

    def test_missing_image_is_rejected(self) -> None:
        worker = self._worker()
        status, payload = worker.submit({"job_id": "01k5j8g0008q3m7b2d6h9n4r5v"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "missing_image")

    def test_missing_job_id_is_rejected(self) -> None:
        status, _ = self._worker().submit({"jpeg_b64": "AAAA"})
        self.assertEqual(status, 400)

    def test_pipeline_failure_settles_as_failed_and_frees_the_slot(self) -> None:
        worker = self._worker(_FakePipeline(fail_with=RuntimeError("boom")))
        job_id = "01k5j8g0008q3m7b2d6h9n4r5v"
        worker.submit(_job_body(job_id))
        settled = _wait(worker, job_id)
        self.assertEqual(settled["status"], WORKER_FAILED)
        self.assertFalse((Path(self._tmp.name) / f"{job_id}.glb").is_file())
        self.assertEqual(worker.submit(_job_body("01k5j8g0008q3m7b2d6h9n4r5w"))[0], 200)

    def test_no_partial_file_is_left_behind(self) -> None:
        worker = self._worker(_FakePipeline(fail_with=RuntimeError("boom")))
        job_id = "01k5j8g0008q3m7b2d6h9n4r5v"
        worker.submit(_job_body(job_id))
        _wait(worker, job_id)
        leftovers = [p.name for p in Path(self._tmp.name).iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_running_job_past_its_budget_reports_failed(self) -> None:
        worker = self._worker(_FakePipeline(delay_s=5.0))
        worker._timeout_s = 0.05
        job_id = "01k5j8g0008q3m7b2d6h9n4r5v"
        worker.submit(_job_body(job_id))
        deadline = time.monotonic() + 2.0
        reported = None
        while time.monotonic() < deadline:
            reported = worker.status(job_id)
            if reported and reported.get("status") == WORKER_FAILED:
                break
            time.sleep(0.01)
        self.assertEqual(reported["status"], WORKER_FAILED)
        self.assertEqual(reported["reason"], "timeout")

    def test_unknown_job_status_is_none(self) -> None:
        self.assertIsNone(self._worker().status("01k5j8g0008q3m7b2d6h9n4r5v"))


if __name__ == "__main__":
    unittest.main()
