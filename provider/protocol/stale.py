from __future__ import annotations

import math

Vec3 = tuple[float, float, float]


def _dist(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def classify_hit(
    cached: Vec3 | None,
    delayed: Vec3 | None,
    same_ray: bool,
    threshold_m: float = 0.12,
) -> str:
    if delayed is None:
        return "no_surface"
    if not same_ray:
        return "placed"
    if cached is None:
        return "stale"
    if _dist(cached, delayed) > threshold_m:
        return "stale"
    return "placed"
