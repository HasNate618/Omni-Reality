"""Layout mode: three cart listings as true-size boxes in a room corner.

The wearer asks for the corner to be filled; this planner answers with one
`place_box` op per listing. Sizes are the seller's stated dimensions, never
something measured, and the spoken copy says so.

Slots are corner-relative (`layout_slot`), not world points: the headset owns
corner detection, so the coordinator never needs room geometry and the whole
path runs with no network. `LayoutPlanner()` with no model is the canned
brain; passing `complete_fn` lets the model choose which listings go where,
falling back to the canned arrangement on anything it does not like.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from coordinator.planner import PlanResult

logger = logging.getLogger(__name__)

CART_PATH = Path(__file__).resolve().parent / "cart.json"

MAX_ITEMS = 3
MAX_EXTENT_M = 4.0
SLOT_LIMIT_M = 8.0

# Measured on this gateway, 2026-09-20: a layout call with a JPEG took 16 s
# once and 39 s the next time -- the model reasons at length before answering,
# and the latency is not stable. That is unusable on a demo's critical path,
# and the shared HTTP client would wait up to 300 s, which is what made the
# headset look dead when the trigger was pulled.
#
# So the model is OFF by default and the canned arrangement answers in ~30 ms.
# Set OMNI_LAYOUT_MODEL=1 to let it try; it is still bounded, and a slow reply
# loses to canned rather than holding the turn open.
MODEL_TIMEOUT_S = 4.0

# Mirrors the `furniture` enum in protocol/schemas/scene_op.json.
FURNITURE_KINDS = frozenset({
    "sofa", "armchair", "coffee_table", "side_table", "bookshelf", "floor_lamp"})


def model_layout_enabled() -> bool:
    import os

    return os.environ.get("OMNI_LAYOUT_MODEL", "").strip() in ("1", "true", "yes")

# Said after the boxes land. Hedged on purpose: the numbers come from a
# listing and the corner from depth sensing, so nothing here is a measurement.
SAY_PLACED = (
    "There's your corner. Sizes come from the listing, so treat it as "
    "approximate. Pull the trigger to push anything around."
)
SAY_NO_CART = "I couldn't read the cart, so there's nothing to place."


def load_cart(path: Path | None = None) -> dict:
    """Read the canned cart. Returns {} when it is missing or malformed."""
    try:
        data = json.loads((path or CART_PATH).read_text())
    except Exception as exc:
        logger.info("cart unreadable exception_class=%s", type(exc).__name__)
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        logger.info("cart has no items list")
        return {}
    return data


def _mm_to_m(listing_mm: dict) -> dict | None:
    try:
        size = {axis: float(listing_mm[axis]) / 1000.0 for axis in ("w", "d", "h")}
    except (KeyError, TypeError, ValueError):
        return None
    if any(not (0 < value <= MAX_EXTENT_M) for value in size.values()):
        return None
    return size


def _slot(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        dx, dz, yaw = float(raw["dx"]), float(raw["dz"]), float(raw["yaw_deg"])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(dx) > SLOT_LIMIT_M or abs(dz) > SLOT_LIMIT_M or not (-180 <= yaw <= 180):
        return None
    return {"type": "layout_slot", "dx": dx, "dz": dz, "yaw_deg": yaw}


def box_op(item: dict, slot: dict | None = None) -> dict | None:
    """One `place_box` model-op, or None when the listing is unusable.

    Returned without op_id/turn_id/stage_epoch: `turn._to_scene_op` stamps
    those and validates the result, exactly as it does for every other kind.
    """
    if not isinstance(item, dict):
        return None
    size = _mm_to_m(item.get("listing_mm") or {})
    target = slot if slot is not None else _slot(item.get("slot"))
    if size is None or target is None:
        logger.info("dropping unusable listing id=%s", str(item.get("id"))[:32])
        return None
    label = str(item.get("label") or item.get("id") or "item")[:40]
    op = {
        "kind": "place_box",
        "target": target,
        "size_m": size,
        "style": {"color": str(item.get("color") or "#3DDCFF"), "label": label},
    }
    # Which shape the headset builds. An unknown kind is dropped rather than
    # sent, so the schema stays the single source of truth for the enum.
    furniture = item.get("furniture")
    if furniture in FURNITURE_KINDS:
        op["furniture"] = furniture
    return op


def canned_ops(cart: dict) -> list[dict]:
    """The fixed arrangement: every listing at the slot the cart names."""
    ops = []
    for item in (cart.get("items") or [])[:MAX_ITEMS]:
        op = box_op(item)
        if op is not None:
            ops.append(op)
    return ops


def apply_model_slots(cart: dict, chosen: object) -> list[dict] | None:
    """Re-slot the cart from a model reply, or None if it is not usable.

    Shape: [{"id": "sofa", "dx": 1.0, "dz": 0.5, "yaw_deg": 0}, ...]. Unknown
    ids, bad numbers, or an empty result all reject the whole reply rather
    than placing a half-understood layout.
    """
    if not isinstance(chosen, list) or not chosen:
        return None
    by_id = {str(item.get("id")): item for item in (cart.get("items") or [])
             if isinstance(item, dict)}
    ops: list[dict] = []
    for entry in chosen[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            return None
        item = by_id.get(str(entry.get("id")))
        slot = _slot(entry)
        if item is None or slot is None:
            return None
        op = box_op(item, slot)
        if op is None:
            return None
        ops.append(op)
    return ops or None


class LayoutPlanner:
    """Canned by default; `complete_fn` opts into model-chosen slots."""

    # Read by turn.py so a layout turn is not diverted into SAM 2 tracking.
    layout = True
    voice_only = False
    perception_qa = False

    def __init__(
        self,
        *,
        cart: dict | None = None,
        complete_fn: Any = None,
        model: str = "qwen3.8-omni-flash",
    ) -> None:
        self.cart = cart if cart is not None else load_cart()
        self.model = model
        self.purpose = "layout-turn"
        self.max_tokens = 256
        self._complete_fn = complete_fn

    async def plan(self, *, pcm, jpeg, envelope, context) -> PlanResult:
        started = time.monotonic()
        if not self.cart.get("items"):
            return PlanResult(ops=[], text=SAY_NO_CART, heard=None)
        ops = None
        if self._complete_fn is not None and model_layout_enabled():
            ops = await self._model_ops(pcm, jpeg)
        if ops is None:
            ops = canned_ops(self.cart)
        return PlanResult(
            ops=ops,
            text=SAY_PLACED if ops else SAY_NO_CART,
            heard=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            proposed_op_count=len(ops),
        )

    async def _model_ops(self, pcm: bytes, jpeg: bytes | None) -> list[dict] | None:
        """Ask the model to place the cart. None on any doubt: canned wins."""
        try:
            from voice.audio import build_voice_messages, pcm_to_wav_bytes
            from yibu_http import extract_text

            extent = self.cart.get("corner_extent_m") or {}
            listing = [
                {"id": item.get("id"), "label": item.get("label"),
                 "w": (item.get("listing_mm") or {}).get("w"),
                 "d": (item.get("listing_mm") or {}).get("d")}
                for item in (self.cart.get("items") or [])[:MAX_ITEMS]
            ]
            prompt = (
                "Lay these out in a room corner. The corner is at the origin; "
                f"x and z run into the room up to {extent.get('x', 2.4)} and "
                f"{extent.get('z', 2.4)} metres. Sizes are millimetres. "
                f"Listings: {json.dumps(listing)}. "
                "Reply with only a JSON array, one entry per listing: "
                '[{"id":"...","dx":metres,"dz":metres,"yaw_deg":-180..180}]. '
                "dx and dz are the centre of the footprint. Keep pieces apart "
                "and inside the corner. No prose."
            )
            messages = build_voice_messages(prompt, wav=pcm_to_wav_bytes(pcm), jpeg=jpeg)
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    self._complete_fn(messages, False), MODEL_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.info("layout: model took over %.1f s, using canned arrangement",
                            MODEL_TIMEOUT_S)
                return None
            logger.info("layout: model replied in %d ms",
                        int((time.monotonic() - started) * 1000))
            text = extract_text(response).strip()
            start, end = text.find("["), text.rfind("]")
            if start < 0 or end <= start:
                logger.info("layout: model reply had no JSON array")
                return None
            ops = apply_model_slots(self.cart, json.loads(text[start:end + 1]))
            if ops is None:
                logger.info("layout: model slots rejected, using canned")
            return ops
        except Exception as exc:
            logger.info("layout model call failed exception_class=%s", type(exc).__name__)
            return None
