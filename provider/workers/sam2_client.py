"""Loopback SAM2 worker client (Task 3)."""
from __future__ import annotations

from typing import Any

import httpx


def inspect_remote(
    *,
    base_url: str,
    frame_id: str,
    jpeg_b64: str,
    target: dict[str, Any],
    phrase: str | None,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/inspect"
    body: dict[str, Any] = {
        "frame_id": frame_id,
        "jpeg_b64": jpeg_b64,
        "target": target,
    }
    if phrase:
        body["phrase"] = phrase
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
            response = client.post(url, json=body)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError("sam2 inspect timed out") from exc
    if not isinstance(data, dict):
        raise RuntimeError("sam2 inspect returned non-object")
    return data
