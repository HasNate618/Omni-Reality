import os
import unittest

import numpy as np


class FakeImagePredictor:
    def __init__(self):
        self.images = []
        self.calls = []

    def set_image(self, arr):
        self.images.append(arr.shape)

    def predict(self, point_coords=None, point_labels=None,
                multimask_output=False):
        self.calls.append((point_coords.tolist(), point_labels.tolist()))
        h, w = self.images[-1][:2]
        m = np.zeros((1, h, w), dtype=bool)
        m[0, h // 3:2 * h // 3, w // 3:2 * w // 3] = True
        return m, np.array([0.9]), None


class SegmentTest(unittest.TestCase):
    def test_groups_clicks_by_object(self):
        from sam2ws.segment import Segmenter
        fake = FakeImagePredictor()
        s = Segmenter(fake)
        with open("sam2ws/tests/data/harness_frames/000000.jpg", "rb") as f:
            raw = f.read()
        out = s.segment_frame(raw, [
            {"x": 1, "y": 2, "label": 1, "obj_id": 1},
            {"x": 3, "y": 4, "label": 0, "obj_id": 1},
            {"x": 5, "y": 6, "label": 1, "obj_id": 2},
        ])
        self.assertEqual([o for o, _ in out], [1, 2])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[0][0], [[1, 2], [3, 4]])
        self.assertTrue(out[0][1].any())


@unittest.skipUnless(os.environ.get("SAM2_GPU_TEST") == "1", "needs GPU + checkpoint")
class SegmentGpuTest(unittest.TestCase):
    def test_center_click_masks_drone(self):
        from sam2.build_sam import build_sam2
        from sam2ws.segment import Segmenter
        model = build_sam2("configs/sam2.1/sam2.1_hiera_t.yaml",
                           "sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
                           device="cuda")
        from sam2.sam2_image_predictor import SAM2ImagePredictor
        s = Segmenter(SAM2ImagePredictor(model))
        with open("sam2ws/tests/data/harness_frames/000000.jpg", "rb") as f:
            raw = f.read()
        out = s.segment_frame(raw, [{"x": 320, "y": 180, "label": 1, "obj_id": 1}])
        self.assertEqual(len(out), 1)
        oid, mask = out[0]
        self.assertEqual(oid, 1)
        self.assertEqual(tuple(mask.shape), (360, 640))
        self.assertGreater(int(mask.sum()), 1000)
