"""Model planner seam for the voice turn (spec §7, §10).

A planner turns one utterance (mic PCM, optional JPEG + capture envelope,
short working context) into spoken text plus at most 3 ModelSceneOps. The
turn loop never sees prompts or raw model output, only a PlanResult.

- StubPlanner: offline, canned ops, for tests and fake-Quest runs.
- YibuPlanner: live yibu omni call over HTTP. Prompting/parsing for spatial
  ops is Member A's lane; when `spatial_ops.parse_model_reply(text) ->
  (say, heard, ops)` exists it is used, otherwise a minimal JSON extractor stands in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from jsonschema import ValidationError

from protocol.validate import validate_instance

logger = logging.getLogger(__name__)

MAX_OPS_PER_TURN = 3
_FRAME_TARGETS = {"capture_hint", "pointing", "image_point", "image_box"}


@dataclass
class PlanResult:
    ops: list[dict]
    text: str
    latency_ms: int = 0
    heard: str | None = None
    audit_id: str | None = None
    proposed_op_count: int = 0  # before validation/capping


class Planner(Protocol):
    async def plan(
        self,
        *,
        pcm: bytes,
        jpeg: bytes | None,
        envelope: dict | None,
        context: list[dict],
    ) -> PlanResult: ...


def fill_frame_id(op: dict, frame_id: str | None) -> dict:
    """Give image-space targets the capture frame_id the model cannot know."""
    if frame_id is None:
        return op
    for key in ("target", "from", "to"):
        target = op.get(key)
        if isinstance(target, dict) and target.get("type") in _FRAME_TARGETS:
            target.setdefault("frame_id", frame_id)
    return op


def accept_model_ops(raw_ops: object, frame_id: str | None) -> list[dict]:
    """Keep schema-valid ModelSceneOps (never repaired), capped at 3."""
    if not isinstance(raw_ops, list):
        return []
    kept: list[dict] = []
    for op in raw_ops:
        if not isinstance(op, dict):
            continue
        op = fill_frame_id(dict(op), frame_id)
        try:
            validate_instance("model_scene_op", op)
        except ValidationError as exc:
            logger.info("dropping invalid model op: %s", exc.message)
            continue
        kept.append(op)
        if len(kept) == MAX_OPS_PER_TURN:
            break
    return kept


class StubPlanner:
    """Canned planner. `ops_factory(envelope)` builds the ops for a turn."""

    def __init__(
        self,
        *,
        ops_factory: Callable[[dict | None], list[dict]] | None = None,
        text: str = "That's it, marked.",
        delay_s: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.ops_factory = ops_factory or _centre_mark
        self.text = text
        self.delay_s = delay_s
        self.error = error
        self.calls: list[dict] = []

    async def plan(self, *, pcm, jpeg, envelope, context) -> PlanResult:
        self.calls.append({"pcm_bytes": len(pcm), "jpeg": jpeg is not None, "context": list(context)})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        raw = self.ops_factory(envelope)
        frame_id = envelope["frame_id"] if envelope else None
        return PlanResult(
            ops=accept_model_ops(raw, frame_id),
            text=self.text,
            heard="(stub)",
            proposed_op_count=len(raw),
        )


def _centre_mark(envelope: dict | None) -> list[dict]:
    if envelope is None:
        return []
    return [
        {
            "kind": "mark",
            "target": {"type": "image_point", "u": 0.5, "v": 0.5},
            "motion": {"kind": "pulse", "period_s": 1.2},
        }
    ]


SYSTEM_PROMPT = """You are a spatial assistant on a mixed-reality headset.
You hear the user's speech (audio) and see what their camera sees (image).
You cannot move matter. You request drawings that the headset places on real surfaces.

Reply with ONLY one JSON object, no prose around it:
{"heard": "<transcript of the user's speech>",
 "say": "<one or two short spoken sentences>",
 "ops": [<0 to 3 drawing ops>]}

A drawing op marks something in the image:
{"kind": "mark", "target": {"type": "image_point", "u": <0..1>, "v": <0..1>},
 "motion": {"kind": "pulse", "period_s": 1.2}}
u is left→right, v is top→bottom, both normalized to the image. Point at the
centre of the object's visible surface. Never give world coordinates.

Rules:
- Only add ops when the user asks you to show, mark, point at, or find something.
- If you cannot tell which object they mean, add no ops and ask them to point or look closer.
- Do not say a mark was placed; the headset confirms placement after you.
- Never guess safety-critical facts (live power, load ratings, food doneness)."""


def _extract_json_object(text: str) -> dict | None:
    """Fallback parser: first {...} object in the reply, fences tolerated."""
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(stripped[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _parse_reply(text: str) -> tuple[str, str | None, list]:
    """(say, heard, raw_ops). Uses Member A's parser when available."""
    try:
        from spatial_ops import parse_model_reply  # type: ignore[import-not-found]
    except ImportError:
        parse_model_reply = None
    if parse_model_reply is not None:
        say, heard, ops = parse_model_reply(text)
        return say, heard, ops
    obj = _extract_json_object(text)
    if obj is None:
        return text.strip(), None, []
    say = obj.get("say") if isinstance(obj.get("say"), str) else ""
    heard = obj.get("heard") if isinstance(obj.get("heard"), str) else None
    return say, heard, obj.get("ops") or []


class YibuPlanner:
    """Live omni call: WAV (+ JPEG) → qwen omni over HTTP Chat Completions."""

    def __init__(
        self,
        *,
        model: str = "qwen3.8-omni-flash",
        purpose: str = "voice-turn",
        max_tokens: int = 256,
        audio_as: str = "data_url",
    ) -> None:
        self.model = model
        self.purpose = purpose
        self.max_tokens = max_tokens
        self.audio_as = audio_as

    def _call(self, messages: list[dict[str, Any]]) -> tuple[str, dict]:
        from yibu_http import chat_completion, require_api_key

        text, _, record = chat_completion(
            api_key=require_api_key(),
            model=self.model,
            messages=messages,
            purpose=self.purpose,
            max_tokens=self.max_tokens,
        )
        return text, record

    async def plan(self, *, pcm, jpeg, envelope, context) -> PlanResult:
        from voice.audio import build_voice_messages, pcm_to_wav_bytes

        history = "\n".join(f"- user: {c.get('heard')} / you: {c.get('said')}" for c in context[-8:])
        prompt = "Respond to the user's speech in the audio."
        if history:
            prompt += "\nRecent turns:\n" + history
        if jpeg is None:
            prompt += "\nNo camera image this turn: add no ops."
        messages = build_voice_messages(
            prompt,
            wav=pcm_to_wav_bytes(pcm),
            jpeg=jpeg,
            system=SYSTEM_PROMPT,
            audio_as=self.audio_as,
        )
        started = time.monotonic()
        text, record = await asyncio.to_thread(self._call, messages)
        latency_ms = int((time.monotonic() - started) * 1000)
        say, heard, raw_ops = _parse_reply(text)
        frame_id = envelope["frame_id"] if envelope and jpeg is not None else None
        ops = accept_model_ops(raw_ops, frame_id) if frame_id else []
        return PlanResult(
            ops=ops,
            text=say,
            latency_ms=latency_ms,
            heard=heard,
            audit_id=record.get("call_id"),
            proposed_op_count=len(raw_ops) if isinstance(raw_ops, list) else 0,
        )
