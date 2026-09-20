"""Model planner seam for the voice turn (spec §7, §10).

A planner turns one utterance (mic PCM, optional JPEG + capture envelope,
short working context) into spoken text plus at most 3 ModelSceneOps. The
turn loop never sees prompts or raw model output, only a PlanResult.

- StubPlanner: offline, canned ops, for tests and fake-Quest runs.
- VoiceStubPlanner: offline audio-only turns, empty ops, transport test caption.
- YibuPlanner: live yibu omni call over HTTP. Prompting/parsing for spatial
  ops is Member A's lane; when `spatial_ops.parse_model_reply(text) ->
  (say, heard, ops)` exists it is used, otherwise a minimal JSON extractor stands in.

YibuPlanner(tracking=True) instead requests one interior image point in
PlanResult.tracking_target; frame identity remains owned by the coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from jsonschema import ValidationError

from omni.reasoner import run_tool_loop
from omni.tools import TOOL_DEFINITIONS
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
    tracking_target: dict | None = None


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
    """Keep schema-valid ModelSceneOps (never repaired), capped at 3.

    At most one `place_procedural` per turn (voice spec §3.1); extras drop.
    """
    if not isinstance(raw_ops, list):
        return []
    kept: list[dict] = []
    seen_procedural = False
    for op in raw_ops:
        if not isinstance(op, dict):
            continue
        if op.get("kind") == "place_procedural":
            if seen_procedural:
                continue
            seen_procedural = True
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


VOICE_STUB_CAPTION = (
    "Voice transport check (test tone — not Omni speech)."
)


class VoiceStubPlanner:
    """Offline headset transport proof: caption + tone synthesizer, no ops."""

    text: str = VOICE_STUB_CAPTION

    def __init__(self, *, perception_qa: bool = False) -> None:
        self.perception_qa = perception_qa

    async def plan(self, *, pcm, jpeg, envelope, context) -> PlanResult:
        return PlanResult(ops=[], text=self.text, heard="(voice-stub)")


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
- Never guess safety-critical facts (live power, load ratings, food doneness).
- Procedural generation (at most one per turn): {"kind": "place_procedural",
  "target": {...}, "elements": [1-6 of {"element": "arrow"|"pointer"|"panel"|
  "cube"|"sphere"|"cylinder", "color": "cyan"|"amber"|"green"|"magenta"|
  "white"|"slate", "size": "small"|"medium"|"large",
  "material": "solid"|"translucent"|"glow", optional "text" (panel/pointer
  only, max 40 chars)}]}. Revise a procedural drawing only by drawing_id:
  {"kind": "revise_procedural", "drawing_id": "...", "action":
  "enlarge"|"shrink"|"rotate_cw"|"rotate_ccw"|"nudge"|"remove",
  "direction": "left"|"right"|"up"|"down"|"forward"|"back" (nudge only)}."""

VOICE_ONLY_SYSTEM_PROMPT = """You are a voice assistant on a mixed-reality headset.
Listen to the user's speech and reply with one or two short spoken sentences in plain text.
Do not use JSON, markdown, drawing instructions, or spatial operations."""


TRACKING_PROMPT = """You hear a user's recorded request and see one Quest camera image.
Select the single visible object the user asks to track/find. Reply ONLY with:
{"heard":"the user's words", "say":"short clarification if needed",
 "track":{"type":"image_point","u":0.5,"v":0.5}}
u is left-to-right and v is top-to-bottom, given as FRACTIONS of the image
between 0 and 1 (e.g. the centre is u=0.5, v=0.5). Never answer in pixels:
"u":320 is wrong, "u":0.5 is right.
Choose a point INSIDE the object's visible solid surface, not background,
a hole, a shadow, or merely the centre of its bounding box. For a laptop,
prefer the middle of its screen or keyboard. Return "track":null if no image
is provided, the object is absent, the request isn't to select an object,
or you cannot determine which instance is meant. Do not invent a target.
Return one point only, not a box, world coordinates, or drawing ops.
Never claim tracking or rendering has started; the application does that later."""


def parse_tracking_reply(
    text: str, width: int | None = None, height: int | None = None
) -> tuple[str, str | None, dict | None]:
    """(say, heard, target). The point may come back as 0..1 fractions or as
    pixels of the image we sent; the model uses both despite the prompt, and a
    rejected point means no tracking at all."""
    obj = _extract_json_object(text) or {}
    say = obj.get("say") if isinstance(obj.get("say"), str) else ""
    heard = obj.get("heard") if isinstance(obj.get("heard"), str) else None
    target = obj.get("track")
    if not isinstance(target, dict) or target.get("type") != "image_point":
        return say, heard, None
    coords = [target.get("u"), target.get("v")]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
           or n < 0 for n in coords):
        return say, heard, None
    u, v = float(coords[0]), float(coords[1])
    if u > 1 or v > 1:
        if not width or not height:
            logger.info("model returned pixels (%s, %s) but the image size is unknown", u, v)
            return say, heard, None
        if u > width or v > height:
            logger.info("model point (%s, %s) is outside the %dx%d image", u, v, width, height)
            return say, heard, None
        logger.info("model returned pixels (%.0f, %.0f); normalised to the %dx%d image", u, v, width, height)
        u, v = u / width, v / height
    return say, heard, {"type": "image_point", "u": u, "v": v}


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
        purpose: str | None = None,
        max_tokens: int = 256,
        audio_as: str = "data_url",
        tracking: bool = False,
        voice_only: bool = False,
        complete_fn: Any | None = None,
        execute_fn: Any | None = None,
    ) -> None:
        self.model = model
        if purpose is None:
            purpose = "voice-only-turn" if voice_only else "voice-turn"
        self.purpose = purpose
        self.max_tokens = max_tokens
        self.audio_as = audio_as
        self.tracking = tracking
        self.voice_only = voice_only
        # Injected in tests; live default (network only inside plan()).
        self._complete_fn = complete_fn or make_live_complete_fn(self)
        self._execute_fn = execute_fn
        self._jobs: Any | None = None
        self._jpeg_b64: str | None = None
        self._frame_id: str | None = None
        self._inspect_fn: Any | None = None
        self._queue_fn: Any | None = None

    def bind_tools(
        self,
        *,
        jobs: Any,
        jpeg_b64: str | None,
        frame_id: str | None,
        inspect_fn: Any | None = None,
        queue_fn: Any | None = None,
    ) -> None:
        """Attach session tool context (called by the turn loop per turn)."""
        self._jobs = jobs
        self._jpeg_b64 = jpeg_b64
        self._frame_id = frame_id
        self._inspect_fn = inspect_fn
        self._queue_fn = queue_fn

    async def _dispatch_tool(self, name: str, arguments: dict) -> Any:
        """Route one model tool call to workers (never to Quest)."""
        if self._execute_fn is not None and name != "emit_scene_ops":
            result = self._execute_fn(name, arguments)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        if self._jobs is None:
            return {"error": "tools_unbound"}
        if name == "inspect_objects":
            from coordinator.jobs import handle_inspect

            return await handle_inspect(
                self._jobs,
                frame_id=arguments.get("frame_id"),
                jpeg_b64=self._jpeg_b64 or "",
                target=arguments.get("target") or {},
                phrase=arguments.get("phrase"),
                current_frame_id=self._frame_id,
                inspect_fn=self._inspect_fn or _default_inspect_fn,
            )
        if name == "start_generation":
            from coordinator.jobs import handle_start_generation

            return await handle_start_generation(
                self._jobs,
                args=dict(arguments),
                current_frame_id=self._frame_id,
                jpeg_b64=self._jpeg_b64,
                queue_fn=self._queue_fn or _default_queue_fn,
            )
        return {"error": "unknown_tool"}

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
        if self.voice_only:
            return await self._plan_voice_only(pcm=pcm, context=context)

        from voice.audio import build_voice_messages, pcm_to_wav_bytes

        history = "\n".join(f"- user: {c.get('heard')} / you: {c.get('said')}" for c in context[-8:])
        prompt = "Respond to the user's speech in the audio."
        if history:
            prompt += "\nRecent turns:\n" + history
        if jpeg is None:
            prompt += "\nNo camera image this turn: " + ("return track:null." if self.tracking else "add no ops.")
        messages = build_voice_messages(
            prompt,
            wav=pcm_to_wav_bytes(pcm),
            jpeg=jpeg,
            system=TRACKING_PROMPT if self.tracking else SYSTEM_PROMPT,
            audio_as=self.audio_as,
        )
        started = time.monotonic()
        logger.info(
            "calling %s: %.2f s audio, %s jpeg bytes, tracking=%s",
            self.model, len(pcm) / 32000, len(jpeg) if jpeg else 0, self.tracking,
        )
        if self.tracking:
            # One call, no tools: tracking only ever wants a point back.
            try:
                text, record = await asyncio.to_thread(self._call, messages)
            except Exception as exc:
                logger.warning("%s call failed: %s: %s", self.model, type(exc).__name__, exc)
                raise
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.debug("%s raw reply: %s", self.model, text[:400].replace("\n", " "))
            say, heard, target = parse_tracking_reply(
                text,
                envelope.get("sent_w") if envelope else None,
                envelope.get("sent_h") if envelope else None,
            )
            return PlanResult(ops=[], text=say, heard=heard, latency_ms=latency_ms,
                              audit_id=record.get("call_id"),
                              tracking_target=target if jpeg is not None and envelope else None)
        frame_id = envelope["frame_id"] if envelope and jpeg is not None else None
        if self._execute_fn is None and self._jobs is None:
            # Legacy single-call path: no tool context bound (offline/tests).
            text, record = await asyncio.to_thread(self._call, messages)
            latency_ms = int((time.monotonic() - started) * 1000)
            say, heard, raw_ops = _parse_reply(text)
            ops = accept_model_ops(raw_ops, frame_id) if frame_id else []
            return PlanResult(
                ops=ops,
                text=say,
                latency_ms=latency_ms,
                heard=heard,
                audit_id=record.get("call_id"),
                proposed_op_count=len(raw_ops) if isinstance(raw_ops, list) else 0,
            )
        # Tool path: bounded tool rounds, then a tools-disabled closing line
        # (skipped when the loop already ended on model text).
        thread = list(messages)
        collected, closing = await run_tool_loop(
            complete_fn=self._complete_fn,
            execute_fn=self._dispatch_tool,
            messages=thread,
            max_rounds=4,
        )
        final_text = closing
        if not final_text:
            final = await self._complete_fn(thread, False)
            from yibu_http import extract_text as _extract_text

            final_text = _extract_text(final)
        say, heard, _ = _parse_reply(final_text)
        latency_ms = int((time.monotonic() - started) * 1000)
        ops = accept_model_ops(collected, frame_id) if frame_id else []
        return PlanResult(
            ops=ops,
            text=say,
            latency_ms=latency_ms,
            heard=heard,
            audit_id=None,
            proposed_op_count=len(collected),
        )

    async def _plan_voice_only(self, *, pcm: bytes, context: list[dict]) -> PlanResult:
        from voice.audio import build_voice_messages, pcm_to_wav_bytes
        from yibu_http import extract_text

        history = "\n".join(
            (f"- user: {c['heard']} / " if c.get('heard') else "- ")
            + f"you: {c.get('said', '')}" for c in context[-8:]
        )
        prompt = "Respond to the user's speech in the audio."
        if history:
            prompt += "\nRecent turns:\n" + history
        messages = build_voice_messages(
            prompt,
            wav=pcm_to_wav_bytes(pcm),
            jpeg=None,
            system=VOICE_ONLY_SYSTEM_PROMPT,
            audio_as=self.audio_as,
        )
        started = time.monotonic()
        response = await self._complete_fn(messages, False)
        text = extract_text(response)
        latency_ms = int((time.monotonic() - started) * 1000)
        say = text.strip()
        audit_id = response.get("id") if isinstance(response, dict) else None
        return PlanResult(
            ops=[],
            text=say,
            latency_ms=latency_ms,
            heard=None,  # Plain-text reply is not a transcript of the user.
            audit_id=audit_id,
            proposed_op_count=0,
        )


def make_live_complete_fn(planner: "YibuPlanner"):
    """Chat Completions backend for the tool loop (network on call only)."""

    async def complete_fn(messages: list[dict[str, Any]], tools_enabled: bool) -> dict[str, Any]:
        from yibu_http import chat_completion, require_api_key

        extra: dict[str, Any] = {}
        if tools_enabled:
            extra = {"tools": TOOL_DEFINITIONS, "tool_choice": "auto"}

        def call() -> dict[str, Any]:
            _text, response_json, _record = chat_completion(
                api_key=require_api_key(),
                model=planner.model,
                messages=list(messages),
                purpose=planner.purpose,
                max_tokens=planner.max_tokens,
                **extra,
            )
            return response_json

        return await asyncio.to_thread(call)

    return complete_fn


def _default_inspect_fn(**kwargs: Any) -> dict:
    import os

    from workers.sam2_client import inspect_remote

    return inspect_remote(
        base_url=os.environ.get("SAM2_WORKER_URL", "http://127.0.0.1:8771"),
        timeout_s=8.0,
        **kwargs,
    )


def _default_queue_fn(**kwargs: Any) -> dict:
    import os

    from workers.gen_client import queue_job

    return queue_job(
        base_url=os.environ.get("GEN_WORKER_URL", "http://127.0.0.1:8772"),
        **kwargs,
    )
