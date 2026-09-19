import io

import numpy as np
from PIL import Image


class Segmenter:
    """Single-frame segmentation over SAM2ImagePredictor.

    Stateless across frames: set_image once per frame, predict per
    click set. Clicks are source-pixel (x, y, label, obj_id); the
    predictor normalizes internally.
    """

    def __init__(self, predictor):
        self.predictor = predictor

    def segment_frame(self, jpeg_bytes, clicks):
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        arr = np.array(img)
        self.predictor.set_image(arr)
        by_obj = {}
        for c in clicks:
            assert c["label"] in (0, 1), c
            by_obj.setdefault(c["obj_id"], []).append(c)
        out = []
        for obj_id, cs in by_obj.items():
            coords = np.array([[c["x"], c["y"]] for c in cs],
                              dtype=np.float32)
            labels = np.array([c["label"] for c in cs], dtype=np.int32)
            masks, scores, _ = self.predictor.predict(
                point_coords=coords, point_labels=labels,
                multimask_output=False,
            )
            mask = np.asarray(masks[0]).astype(bool)
            assert mask.ndim == 2, mask.shape
            out.append((obj_id, mask))
        return out
