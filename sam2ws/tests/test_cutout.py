import io
import unittest

import numpy as np
from PIL import Image


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


class CutoutTest(unittest.TestCase):
    def test_bbox_crop_with_margin_and_alpha(self):
        from sam2ws.cutout import cutout_rgba
        m = np.zeros((10, 20), dtype=bool)
        m[2:6, 4:12] = True  # bbox 8x4 at (4,2)
        out = cutout_rgba(_jpeg(), m, margin=0.0)
        self.assertEqual(out.size, (8, 4))
        alpha = np.array(out.split()[3])
        self.assertEqual(int((alpha > 0).sum()), int(m.sum()))
        self.assertEqual(tuple(np.array(out.convert("RGB"))[0, 0]), (10, 20, 30))

    def test_margin_expands_and_clamps(self):
        from sam2ws.cutout import cutout_rgba
        m = np.zeros((10, 20), dtype=bool)
        m[0:2, 0:3] = True  # corner: margin clamps at edges
        out = cutout_rgba(_jpeg(), m, margin=0.5)
        self.assertTrue(out.size[0] > 3 and out.size[1] > 2)
        self.assertTrue(out.size[0] <= 20 and out.size[1] <= 10)

    def test_empty_mask_raises(self):
        from sam2ws.cutout import cutout_rgba
        with self.assertRaises(AssertionError):
            cutout_rgba(_jpeg(), np.zeros((10, 20), dtype=bool))
