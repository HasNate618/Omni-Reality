"""Pre-baked artifact registry (spec §5.3).

A listing name that maps to an artifact id is placeable without queueing a
worker. This exists because generation is serialized by
``_session_generation_busy``: a demonstration that places several listings
cannot generate them concurrently.

Whether a listing is pre-baked is coordinator configuration, never a model
input, and the registry deliberately does not carry extents — the model still
has to state sizes, so the honesty gate is unchanged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from coordinator.listings import normalize_name

logger = logging.getLogger(__name__)

Registry = dict[str, str]


def load_registry(path: Path) -> Registry:
    """Read a name → artifact id map. Any problem yields an empty registry."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    registry: Registry = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            registry[normalize_name(key)] = value
    return registry


def lookup(registry: Registry, name: str) -> str | None:
    """Artifact id configured for this listing name, or None."""
    return registry.get(normalize_name(name))
