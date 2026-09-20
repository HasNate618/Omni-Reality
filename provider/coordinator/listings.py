"""Listing memory (spec §4): sizes the model stated, keyed by name.

Rows are recorded when a listing is placed, never on a guess. A row with no
stated sizes is not a row this store can hold, so every row is placeable and
the honesty gate lives in the caller's validation, not here.
"""

from __future__ import annotations

from typing import Any


def normalize_name(name: str) -> str:
    """Fold case and collapse whitespace so repeats update one row."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split()).strip().lower()


class ListingMemory:
    """Session-scoped listing rows keyed by normalized name."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def record(
        self,
        name: str,
        extent_m: list[float],
        source_frame_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        key = normalize_name(name)
        row = {
            "name": " ".join(name.split()) if isinstance(name, str) else "",
            "extent_m": [float(axis) for axis in extent_m],
            "source_frame_id": source_frame_id,
            "source": source,
        }
        self._rows[key] = row
        return row

    def get(self, name: str) -> dict[str, Any] | None:
        return self._rows.get(normalize_name(name))

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows.values())

    def clear(self) -> None:
        self._rows.clear()
