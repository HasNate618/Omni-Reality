"""Model planner seam for the voice turn (spec §7, §10).

A planner turns one utterance (mic PCM, optional JPEG + capture envelope,
short working context) into spoken text plus at most 3 ModelSceneOps. The
turn loop never sees prompts or raw model output, only a PlanResult.

- StubPlanner: offline, canned ops, for tests and fake-Quest runs.
- VoiceStubPlanner: offline audio-only turns, empty ops, transport test caption.
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


def _tool_declares(name: str, param: str) -> bool:
    """Whether TOOL_DEFINITIONS declares parameter `param` for tool `name`."""
    for tool in TOOL_DEFINITIONS:
        fn = tool.get("function") or {}
        if fn.get("name") == name:
            props = (fn.get("parameters") or {}).get("properties") or {}
            return param in props
    return False


def fill_tool_args(name: str, arguments: dict, frame_id: str | None) -> dict:
    """Give tool arguments the capture frame_id the model cannot know.

    The frame id is a 26-char ULID minted on Quest and never shown to the model, so
    a frame-anchored tool call can only validate if the coordinator supplies it --
    the same reason `fill_frame_id` exists for model-authored ops. Tools need their
    own filler because the id arrives in two shapes here: a top-level `frame_id`
    parameter (`inspect_objects` requires one) and a frame-space `target`.

    Unlike `fill_frame_id` this OVERWRITES rather than setdefaulting: the model has
    no way to know a real id, so anything it supplies is a guess, and the capture's
    own id is the only correct answer.
    """
    args = dict(arguments)
    if frame_id is None:
        return args
    if _tool_declares(name, "frame_id"):
        args["frame_id"] = frame_id
    target = args.get("target")
    if isinstance(target, dict) and target.get("type") in _FRAME_TARGETS:
        # Copy instead of mutating: `dict(arguments)` above is shallow, so this
        # `target` is still the caller's object.
        args["target"] = dict(target, frame_id=frame_id)
    return args


CONTEXT_DRAWING_TURNS = 4


def _recent_drawing_ids(context: list[dict]) -> str:
    """Recent placements as prompt lines, for `revise_procedural`.

    A revision op is keyed on a drawing_id that Quest mints (a 26-char ULID), so
    the model can neither invent one nor revise anything unless it is told the
    ids it just created. Each id is paired with the turn it came from so the
    model can pick the right object. Newest last, duplicates dropped.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for entry in context[-CONTEXT_DRAWING_TURNS:]:
        ids = [d for d in (entry.get("drawing_ids") or []) if isinstance(d, str)]
        if not ids:
            continue
        said = str(entry.get("said") or "").strip()
        for drawing_id in ids:
            if drawing_id in seen:
                continue
            seen.add(drawing_id)
            lines.append(f"- {drawing_id}  ({said})" if said else f"- {drawing_id}")
    return "\n".join(lines)


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
  "enlarge"|"shrink"|"rotate_cw"|"rotate_ccw"|"nudge"|"remove"|"swap",
  "direction": "left"|"right"|"up"|"down"|"forward"|"back" (nudge only),
  "target": {"type": "drawing", "drawing_id": "..."} (swap only: ask for it
  when the wearer wants one item to take on another item's size and shape; the
  target names that other drawing)}."""

TOOLS_SYSTEM_PROMPT = """You are a spatial assistant on a mixed-reality headset.
You hear the user's speech (audio) and see what their camera sees (image).
You cannot move matter. You call tools; the headset does the placing.

Call a tool by name. You never author geometry: no world coordinates, no poses, no
distances, no object ids, no drawing ids you were not given.

place_item -- place a real-size item in the room.
  Use this whenever the wearer wants something from a listing, page, catalogue,
  menu or price tag brought into the room: "bring this to life", "put this in the
  corner", "how big would that be", "place the side table".
  You MUST pass all three sizes in metres, READ FROM THE IMAGE or HEARD FROM THE
  WEARER. Never estimate from memory, never infer from how large it looks in the
  photo, never guess a missing axis. If you cannot read all three sizes, ASK THE
  WEARER instead of calling the tool. A size you invented is a lie the wearer will
  stand next to.
  name is what the listing calls the item, verbatim. target is where to put it in
  the current view -- use capture_hint for "in front of me" or an empty area.

inspect_objects -- find objects in the current frame near a target.
start_generation -- queue mesh generation for an object returned by inspect_objects.
emit_scene_ops -- propose drawing ops (mark / label / ghost / connect /
  place_procedural / revise_procedural). This CANNOT place a generated mesh; use
  place_item for that.

Drawing ops, for emit_scene_ops only:
{"kind": "mark", "target": {"type": "image_point", "u": <0..1>, "v": <0..1>},
 "motion": {"kind": "pulse", "period_s": 1.2}}
u is left to right, v is top to bottom, both normalized to the image. Point at the
centre of the object's visible surface.
Procedural generation (at most one per turn): {"kind": "place_procedural",
  "target": {...}, "elements": [1-6 of {"element": "arrow"|"pointer"|"panel"|
  "cube"|"sphere"|"cylinder", "color": "cyan"|"amber"|"green"|"magenta"|
  "white"|"slate", "size": "small"|"medium"|"large",
  "material": "solid"|"translucent"|"glow", optional "text" (panel/pointer
  only, max 40 chars)}]}. Revise a procedural drawing only by drawing_id:
  {"kind": "revise_procedural", "drawing_id": "...", "action":
  "enlarge"|"shrink"|"rotate_cw"|"rotate_ccw"|"nudge"|"remove"|"swap",
  "direction": "left"|"right"|"up"|"down"|"forward"|"back" (nudge only),
  "target": {"type": "drawing", "drawing_id": "..."} (swap only: the drawing
  whose size and shape this one should take on)}.

Rules:
- Say one or two short sentences along with each tool call.
- Only propose drawing ops when the user asks you to show, mark, point at or find
  something.
- If you cannot tell which object they mean, ask them to point or look closer.
- Do not claim something was placed; the headset confirms placement after you.
- Never guess safety-critical facts (live power, load ratings, food doneness).
"""

VOICE_ONLY_SYSTEM_PROMPT = """You are a voice assistant on a mixed-reality headset.
Listen to the user's speech and reply with one or two short spoken sentences in plain text.
Do not use JSON, markdown, drawing instructions, or spatial operations."""


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
    raw_say = obj.get("say")
    say = raw_say if isinstance(raw_say, str) else ""
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
        self.voice_only = voice_only
        # Injected in tests; live default (network only inside plan()).
        self._complete_fn = complete_fn or make_live_complete_fn(self)
        self._execute_fn = execute_fn
        self._jobs: Any | None = None
        self._jpeg_b64: str | None = None
        self._frame_id: str | None = None
        self._inspect_fn: Any | None = None
        self._queue_fn: Any | None = None
        self._listings: Any = None
        self._prebaked: dict[str, str] = {}
        self._coordinator_ops: list[dict] = []

    def bind_tools(
        self,
        *,
        jobs: Any,
        jpeg_b64: str | None,
        frame_id: str | None,
        inspect_fn: Any | None = None,
        queue_fn: Any | None = None,
        listings: Any | None = None,
        prebaked: dict[str, str] | None = None,
    ) -> None:
        """Attach session tool context (called by the turn loop per turn)."""
        self._jobs = jobs
        self._jpeg_b64 = jpeg_b64
        self._frame_id = frame_id
        self._inspect_fn = inspect_fn
        self._queue_fn = queue_fn
        self._listings = listings
        self._prebaked = prebaked or {}
        self._coordinator_ops = []

    async def _dispatch_tool(self, name: str, arguments: dict) -> Any:
        """Route one model tool call to workers (never to Quest)."""
        arguments = fill_tool_args(name, arguments, self._frame_id)
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
        if name == "place_item":
            from coordinator.listings import handle_place_item

            result, op = await handle_place_item(
                self._jobs,
                listings=self._listings,
                args=dict(arguments),
                current_frame_id=self._frame_id,
                prebaked=self._prebaked,
                queue_fn=self._queue_fn or _default_queue_fn,
                jpeg_b64=self._jpeg_b64,
            )
            if op is not None:
                self._coordinator_ops.append(op)
            return result
        return {"error": "unknown_tool"}

    def _merge_ops(self, model_ops: list[dict], frame_id: str | None) -> list[dict]:
        """Coordinator-authored ops lead, model ops follow, cap applies to both.

        Coordinator ops are already frame-anchored and are validated by
        _to_scene_op in the turn loop, which is the single send-time gate.
        """
        merged = list(self._coordinator_ops) + list(model_ops)
        return merged[:MAX_OPS_PER_TURN]

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
        placed = _recent_drawing_ids(context)
        if placed:
            prompt += (
                "\nObjects already placed (revise these by their exact id; "
                "never invent one):\n" + placed
            )
        if jpeg is None:
            prompt += "\nNo camera image this turn: add no ops."
        # The tool path must NOT be handed the legacy ops prompt. That prompt says
        # "reply with ONLY one JSON object" and never names a tool, so the model
        # has no instructed route to place_item -- it answers with prose ops, and
        # accept_model_ops drops any place_generated it emits, by design. Verified
        # that no prompt anywhere mentioned a tool before this.
        tools_available = self._execute_fn is not None or self._jobs is not None
        messages = build_voice_messages(
            prompt,
            wav=pcm_to_wav_bytes(pcm),
            jpeg=jpeg,
            system=TOOLS_SYSTEM_PROMPT if tools_available else SYSTEM_PROMPT,
            audio_as=self.audio_as,
        )
        started = time.monotonic()
        frame_id = envelope["frame_id"] if envelope and jpeg is not None else None
        if self._execute_fn is None and self._jobs is None:
            # Legacy single-call path: no tool context bound (offline/tests).
            text, record = await asyncio.to_thread(self._call, messages)
            latency_ms = int((time.monotonic() - started) * 1000)
            say, heard, raw_ops = _parse_reply(text)
            ops = self._merge_ops(
                accept_model_ops(raw_ops, frame_id) if frame_id else [], frame_id
            )
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
        ops = self._merge_ops(
            accept_model_ops(collected, frame_id) if frame_id else [], frame_id
        )
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
