"""Loopback image-to-3D generation worker client (Task 4)."""
from __future__ import annotations

from typing import Any

import httpx


class BusyError(Exception):
    pass


def queue_job(
    *,
    base_url: str,
    job_id: str,
    frame_id: str,
    jpeg_b64: str,
    prompt: str | None,
    mask_png_b64: str | None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/jobs"
    body: dict[str, Any] = {
        "job_id": job_id,
        "frame_id": frame_id,
        "jpeg_b64": jpeg_b64,
    }
    if prompt:
        body["prompt"] = prompt
    if mask_png_b64:
        body["mask_png_b64"] = mask_png_b64
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.post(url, json=body)
    if response.status_code == 409:
        raise BusyError("generation worker busy")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("queue_job non-object")
    return data


def get_job(*, base_url: str, job_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/jobs/" + job_id
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        response = client.get(url)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("get_job non-object")
    return data
