"""OpenAI tool definitions and model op filtering."""

from __future__ import annotations

from jsonschema import ValidationError

from protocol.validate import validate_instance

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "inspect_objects",
            "description": "Find salient objects in the current frame near a target.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_id", "target"],
                "properties": {
                    "frame_id": {"type": "string"},
                    "target": {"type": "object"},
                    "phrase": {"type": "string", "maxLength": 40},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_generation",
            "description": "Queue async mesh generation for a detected object.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "object_id": {"type": "string"},
                    "target": {"type": "object"},
                    "prompt": {"type": "string", "maxLength": 80},
                    "frame_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_item",
            "description": (
                "Place an item you have sized onto a surface in the room. "
                "State the sizes you read or were given; never guess them. "
                "If you do not have all three sizes, ask the wearer instead."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "extent_m", "target"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 40},
                    "extent_m": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number", "minimum": 0.05, "maximum": 3.0},
                    },
                    "target": {"type": "object"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emit_scene_ops",
            "description": "Propose up to three scene operations (mark/label/ghost/connect/place_procedural/revise_procedural).",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ops"],
                "properties": {
                    "ops": {"type": "array", "maxItems": 3, "items": {"type": "object"}},
                },
            },
        },
    },
]

_COORDINATOR_KINDS = frozenset({"place_generated", "place_known"})


def accept_model_ops(raw_ops: list, *, max_ops: int = 3) -> list[dict]:
    """Validate model ops; drop mesh placement kinds and cap count.

    At most one `place_procedural` per turn (voice spec §3.1); extras drop.
    """
    accepted: list[dict] = []
    seen_procedural = False
    for item in raw_ops:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind in _COORDINATOR_KINDS:
            continue
        if kind == "place_procedural":
            if seen_procedural:
                continue
            seen_procedural = True
        try:
            validate_instance("model_scene_op", item)
        except ValidationError:
            continue
        accepted.append(item)
        if len(accepted) >= max_ops:
            break
    return accepted[:max_ops]
