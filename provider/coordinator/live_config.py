"""Live coordinator startup checks (fail before WebSockets, no provider calls)."""

from __future__ import annotations

from yibu_audit import ApiKeyConfigurationError, ensure_env_api_key


def ensure_live_planner_config(planner_kind: str) -> None:
    """Require YIBU_API_KEY up front for every live `--planner yibu` mode.

    All live modes reach the provider on their first turn: the tool planner,
    `--voice-only`, and `--perception-qa`. Checking here fails fast and says
    why. Without it the server binds happily and then every single turn fails
    behind the misleading line "Sorry, I couldn't reach the model", which reads
    as a network fault rather than a missing key.
    """
    if planner_kind == "yibu":
        ensure_env_api_key("YIBU_API_KEY")
