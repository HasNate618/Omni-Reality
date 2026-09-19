from __future__ import annotations


def spec_uv_to_pca_viewport(u: float, v: float) -> tuple[float, float]:
    return (u, 1.0 - v)


def spec_uv_to_pixel_center(
    u: float, v: float, sent_w: float, sent_h: float
) -> tuple[float, float]:
    return (u * sent_w - 0.5, v * sent_h - 0.5)
