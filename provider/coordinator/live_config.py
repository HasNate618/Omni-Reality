"""Live coordinator startup checks (fail before WebSockets, no provider calls)."""

from __future__ import annotations

from yibu_audit import ApiKeyConfigurationError, ensure_env_api_key


def ensure_live_voice_only_config(planner_kind: str, voice_only: bool) -> None:
    """Require YIBU_API_KEY when starting `--planner yibu --voice-only`."""
    if planner_kind == "yibu" and voice_only:
        ensure_env_api_key("YIBU_API_KEY")
