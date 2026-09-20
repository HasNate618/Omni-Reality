import io

import numpy as np
from PIL import Image


def cutout_rgba(jpeg_bytes, mask, margin=0.05):
    """Crop to the mask bbox (+ margin) and return an RGBA image whose
    alpha channel is the SAM2 mask. This replaces rembg in the 3D-gen
    path; downstream (resize_foreground, TripoSR) is unchanged.

    mask: 2D bool array matching the JPEG dimensions.
    margin: fraction of bbox width/height added on each side.
    """
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    w, h = img.size
    m = np.asarray(mask) > 0
    assert m.ndim == 2 and m.shape == (h, w), (m.shape, (h, w))
    ys, xs = np.nonzero(m)
    assert len(xs) > 0, "empty mask"
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    x0 = max(0, int(x0 - bw * margin))
    y0 = max(0, int(y0 - bh * margin))
    x1 = min(w - 1, int(x1 + bw * margin))
    y1 = min(h - 1, int(y1 + bh * margin))
    crop = img.crop((x0, y0, x1 + 1, y1 + 1))
    alpha = Image.fromarray((m[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8))
    out = crop.convert("RGBA")
    out.putalpha(alpha)
    return out
