"""Redacted VoiceBootstrap coordinator logs (counts and types only)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "VoiceBootstrap"
_COMPONENT = "coordinator"

_ALLOWED_STRING_KEYS = frozenset(
    {"event", "component", "exception_class", "mode", "voice_gate", "phase", "reason"}
)


def _looks_like_payload(text: str) -> bool:
    if " " in text:
        return True
    lower = text.lower()
    if lower.startswith("http") or lower.startswith("ws://"):
        return True
    if len(text) >= 32 and sum(c.isalnum() or c in "+/=" for c in text) >= len(text) - 2:
        return True
    return False


def is_allowed_field(key: str, value: Any) -> bool:
    if not key:
        return False
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, str):
        if key not in _ALLOWED_STRING_KEYS:
            return False
        if len(value) > 64:
            return False
        if _looks_like_payload(value):
            return False
        return True
    return False


def _emit(event: str, **fields: Any) -> None:
    parts = [f"{_PREFIX} component={_COMPONENT} event={event}"]
    for key, value in fields.items():
        if not is_allowed_field(key, value):
            raise ValueError(f"VoiceBootstrap field rejected: {key}")
        if value is None:
            continue
        if isinstance(value, bool):
            parts.append(f"{key}={'true' if value else 'false'}")
        else:
            parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def connection_open() -> None:
    _emit("connection_open")


def connection_close() -> None:
    _emit("connection_close")


def utterance_end_accepted(pcm_bytes: int) -> None:
    _emit("utterance_end_accepted", pcm_bytes=pcm_bytes)


def turn_started(*, turn_id: int, mode: str, pcm_bytes: int) -> None:
    _emit("turn_started", turn_id=turn_id, mode=mode, pcm_bytes=pcm_bytes)


def planner_complete(*, turn_id: int, latency_ms: int | None, ops_count: int) -> None:
    _emit(
        "planner_complete",
        turn_id=turn_id,
        latency_ms=latency_ms if latency_ms is not None else -1,
        ops_count=ops_count,
    )


def synth_result(*, turn_id: int, voice_gate: str, audio_bytes: int) -> None:
    _emit("synth_result", turn_id=turn_id, voice_gate=voice_gate, audio_bytes=audio_bytes)


def planner_failed(*, turn_id: int, exception_class: str) -> None:
    _emit("planner_failed", turn_id=turn_id, exception_class=exception_class)


def synth_failed(*, turn_id: int, exception_class: str) -> None:
    _emit("synth_failed", turn_id=turn_id, exception_class=exception_class)


def perception_frame(event: str, *, reason: str | None = None, jpeg_bytes: int = 0) -> None:
    _emit(event, reason=reason, jpeg_bytes=jpeg_bytes)


def planner_mode_label(planner: Any) -> str:
    if getattr(planner, "perception_qa", False):
        return "perception_qa"
    if planner is None:
        return "mark"
    voice_only = getattr(planner, "voice_only", False)
    if voice_only:
        return "voice_only"
    class_name = type(planner).__name__
    if class_name == "VoiceStubPlanner":
        return "voice_stub"
    if class_name == "StubPlanner":
        return "stub"
    if class_name == "YibuPlanner":
        return "yibu"
    return "unknown"
