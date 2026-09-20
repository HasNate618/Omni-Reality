"""Bounded image-grounded tutorial plans and a model-independent controller."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import uuid

MAX_GUIDE_OBJECTS = 3
MAX_GUIDE_STEPS = 8


def _text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


@dataclass(frozen=True)
class GuidePlan:
    title: str
    objects: list[dict]
    steps: list[dict]

    @classmethod
    def from_dict(cls, raw: object) -> GuidePlan:
        """Reject the entire plan on invalid references/coordinates; never repair IDs."""
        if not isinstance(raw, dict) or set(raw) != {"title", "objects", "steps"}:
            raise ValueError("guide requires title, objects and steps only")
        objects, steps = raw["objects"], raw["steps"]
        if not _text(raw["title"], 120):
            raise ValueError("invalid guide title")
        if not isinstance(objects, list) or not 1 <= len(objects) <= MAX_GUIDE_OBJECTS:
            raise ValueError("guide requires one to three objects")
        ids: set[str] = set()
        points: list[tuple[float, float]] = []
        for obj in objects:
            if not isinstance(obj, dict) or set(obj) != {"id", "label", "u", "v"}:
                raise ValueError("object requires id, label and image UV only")
            oid = obj["id"]
            if not isinstance(oid, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", oid) or oid in ids:
                raise ValueError("invalid or duplicate object id")
            if not _text(obj["label"], 80):
                raise ValueError("invalid object label")
            point = (obj["u"], obj["v"])
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1 for x in point):
                raise ValueError("object coordinates must be normalized finite numbers")
            if any(math.dist(point, other) < 0.05 for other in points):
                raise ValueError("duplicate object seed")
            ids.add(oid)
            points.append(point)
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_GUIDE_STEPS:
            raise ValueError("guide requires one to eight steps")
        used: set[str] = set()
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or set(step) != {"index", "instruction", "highlight"}:
                raise ValueError("invalid step fields")
            if type(step["index"]) is not int or step["index"] != index or not _text(step["instruction"], 240):
                raise ValueError("steps require contiguous indices and bounded instructions")
            highlights = step["highlight"]
            if not isinstance(highlights, list) or not 1 <= len(highlights) <= MAX_GUIDE_OBJECTS:
                raise ValueError("each step requires one to three highlights")
            if any(not isinstance(oid, str) or oid not in ids for oid in highlights) or len(set(highlights)) != len(highlights):
                raise ValueError("unknown or repeated highlight id")
            used.update(highlights)
        if used != ids:
            raise ValueError("every tracked object must be used by the tutorial")
        return cls(raw["title"].strip(), [dict(obj) for obj in objects],
                   [dict(step, highlight=list(step["highlight"])) for step in steps])

    @property
    def tracking_targets(self) -> list[dict]:
        return [{"type": "image_point", "u": obj["u"], "v": obj["v"], "label": obj["label"]}
                for obj in self.objects]


@dataclass
class GuideSession:
    guide_id: str
    title: str
    objects: dict[str, int]
    steps: list[dict]
    current_step: int = 0
    active: bool = True

    @classmethod
    def from_plan(cls, plan: GuidePlan, guide_id: str | None = None) -> GuideSession:
        return cls(guide_id or uuid.uuid4().hex, plan.title,
                   {obj["id"]: index for index, obj in enumerate(plan.objects, 1)},
                   [dict(step, highlight=list(step["highlight"])) for step in plan.steps])

    def step_payload(self) -> dict | None:
        if not self.active:
            return None
        step = self.steps[self.current_step]
        return {"guide_id": self.guide_id, "title": self.title,
                "step_index": self.current_step, "total_steps": len(self.steps),
                "active_obj_ids": [self.objects[oid] for oid in step["highlight"]],
                "instruction": step["instruction"]}

    def advance(self) -> dict | None:
        if not self.active:
            return None
        if self.current_step + 1 >= len(self.steps):
            self.active = False
            return None
        self.current_step += 1
        return self.step_payload()

    def repeat(self) -> dict | None:
        return self.step_payload()

    def stop(self) -> None:
        self.active = False


def classify_guide_command(transcript: str) -> str:
    """Recognize full control phrases only; incidental 'next' must not advance."""
    words = re.sub(r"[^a-z0-9\s]", " ", transcript.lower()).split()
    if words[:2] == ["hey", "omni"]:
        words = words[2:]
    elif words[:1] == ["omni"]:
        words = words[1:]
    words = [word for word in words if word != "please"]
    phrase = " ".join(words)
    if phrase in {"next", "next step", "okay next", "ok next", "done", "im done", "i m done", "finished", "continue", "go on", "okay next step", "ok next step"}:
        return "next"
    if phrase in {"repeat", "repeat that", "repeat the step", "say that again", "again"}:
        return "repeat"
    if phrase in {"stop", "stop guide", "stop the guide", "stop tutorial", "stop the tutorial", "cancel", "cancel guide", "cancel the guide"}:
        return "stop"
    return "unknown"
