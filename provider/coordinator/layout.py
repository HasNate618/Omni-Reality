"""Pack arithmetic for a row of placed listings (spec §7.1).

Pure functions over listing extents. They never see world coordinates: the
result is a distance along the placement frame's width axis, which Quest
applies. The model never receives these numbers as world metres.
"""

from __future__ import annotations

GAP_M = 0.05


def pack_offsets(extents: list[list[float]], gap: float = GAP_M) -> list[float]:
    """Centre offset along the width axis for each item, in input order.

    Offsets are relative to the first item's centre, which is the hit. A lone
    placement therefore lands where the wearer pointed instead of half its own
    width to the side. Each later item clears the running width plus one gap.
    """
    if not extents:
        return []
    offsets = [0.0]
    cursor = float(extents[0][0]) / 2.0
    for extent in extents[1:]:
        width = float(extent[0])
        offsets.append(cursor + gap + width / 2.0)
        cursor += width + gap
    return offsets


def run_length(extents: list[list[float]], gap: float = GAP_M) -> float:
    """Total width of the packed row, gaps included. Empty is 0.0."""
    if not extents:
        return 0.0
    widths = sum(float(extent[0]) for extent in extents)
    return widths + gap * (len(extents) - 1)
