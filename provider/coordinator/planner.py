"""Model planner seam for the voice turn (spec §7, §10).

A planner turns one utterance (mic PCM, optional JPEG + capture envelope,
short working context) into spoken text plus at most 3 ModelSceneOps. The
turn loop never sees prompts or raw model output, only a PlanResult.

- StubPlanner: offline, canned ops, for tests and fake-Quest runs.
- VoiceStubPlanner: offline audio-only turns, empty ops, transport test caption.
- YibuPlanner: live yibu omni call over HTTP. Prompting/parsing for spatial
  ops is Member A's lane; when `spatial_ops.parse_model_reply(text) ->
  (say, heard, ops)` exists it is used, otherwise a minimal JSON extractor stands in.

YibuPlanner(tracking=True) instead requests one interior image point per object
the wearer named (up to MAX_TRACKED_OBJECTS) in PlanResult.tracking_targets;
frame identity remains owned by the coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from jsonschema import ValidationError

from omni.reasoner import run_tool_loop
from omni.tools import TOOL_DEFINITIONS
from protocol.validate import validate_instance
from coordinator.guide import GuidePlan

logger = logging.getLogger(__name__)

MAX_OPS_PER_TURN = 3
_FRAME_TARGETS = {"capture_hint", "pointing", "image_point", "image_box"}

# SAM 2 pays memory attention per object, so each extra tracked object costs
# roughly +70 ms per frame (measured; docs/omni-sam2-streaming.md). Three fit the
# 333 ms budget at the default 3 fps stream; four do not, and past the budget the
# post-seed catch-up starts failing.
MAX_TRACKED_OBJECTS = 3
# Two requested points closer than this (normalised distance) are the same
# object named twice, which would waste a whole tracker slot on a duplicate.
MIN_TARGET_SEPARATION = 0.05


@dataclass
class PlanResult:
    ops: list[dict]
    text: str
    latency_ms: int = 0
    heard: str | None = None
    audit_id: str | None = None
    proposed_op_count: int = 0  # before validation/capping
    tracking_targets: list[dict] = field(default_factory=list)
    guide_plan: GuidePlan | None = None

    @property
    def tracking_target(self) -> dict | None:
        """First selected point, for callers that only handle one object."""
        return self.tracking_targets[0] if self.tracking_targets else None


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


TRACKING_PROMPT = """You hear a user's recorded request and see one Quest camera image.
Select every visible object the user asks to track/find, at most 3. Reply ONLY with:
{"heard":"the user's words", "say":"short clarification if needed",
 "track":[{"label":"the object, two words","type":"image_point","u":0.5,"v":0.5}]}
u is left-to-right and v is top-to-bottom, given as FRACTIONS of the image
between 0 and 1 (e.g. the centre is u=0.5, v=0.5). Never answer in pixels:
"u":320 is wrong, "u":0.5 is right.
Choose a point INSIDE the object's visible solid surface, not background,
a hole, a shadow, or merely the centre of its bounding box. For a laptop,
prefer the middle of its screen or keyboard. Return "track":[] if no image
is provided, the objects are absent, the request isn't to select an object,
or you cannot determine which instance is meant. Do not invent a target.
Give ONE point per distinct object and never two points on the same object.
Return points only, not boxes, world coordinates, or drawing ops.
Never claim tracking or rendering has started; the application does that later.

If and ONLY if the user asks for a tutorial, step-by-step help, or how to do a
physical task (for example "show me how to organize this desk"), instead return:
{"heard":"the user's words", "say":"", "guide": {
 "title":"Organize your desk",
 "objects":[{"id":"mug","label":"Mug","u":0.7,"v":0.6}],
 "steps":[{"index":0,"instruction":"Move the mug to the right.","highlight":["mug"]}]}}
A guide has 1-3 DISTINCT visible physical objects and 1-8 concise actionable
steps, preferably three; each instruction is at most 240 characters.
Use the exact supplied image and audio. Never invent
objects or image coordinates. IDs are unique short alphanumeric identifiers;
all highlight IDs must exist in objects and all objects must be used. Indices
start at zero and are contiguous. The first highlight is the primary object;
remaining highlights are secondary references. Points must lie on each visible
object's surface, normalized 0..1; no pixels, world coordinates or extra fields.
Keep the whole plan bounded and self-contained. If no image, the task is unclear,
or needed objects are not visible, return track:[] and ask a short clarification.
Ordinary requests to track/find/mark objects must use track, NEVER guide.
Never generate a tutorial in response to a standalone next/repeat/stop command."""


def parse_tracking_reply(
    text: str, width: int | None = None, height: int | None = None
) -> tuple[str, str | None, list[dict]]:
    """(say, heard, targets). One entry per object the model selected, capped at
    MAX_TRACKED_OBJECTS. An unusable entry is dropped rather than failing the
    whole reply, so one bad point does not cost the objects beside it."""
    obj = _extract_json_object(text) or {}
    say = obj.get("say") if isinstance(obj.get("say"), str) else ""
    heard = obj.get("heard") if isinstance(obj.get("heard"), str) else None
    raw = obj.get("track")
    # A bare object is the older single-object reply shape; still accepted.
    entries = raw if isinstance(raw, list) else [raw]
    # A top-level "label" belongs to the single-object shape.
    fallback_label = obj.get("label") if isinstance(obj.get("label"), str) else None
    targets: list[dict] = []
    for entry in entries:
        point = _one_point(entry, width, height, fallback_label)
        if point is None:
            continue
        if any(math.dist((point["u"], point["v"]), (kept["u"], kept["v"]))
               < MIN_TARGET_SEPARATION for kept in targets):
            logger.info("dropping duplicate point at (%.3f, %.3f)", point["u"], point["v"])
            continue
        targets.append(point)
        if len(targets) == MAX_TRACKED_OBJECTS:
            break
    if targets:
        # Names what the model believes it selected. A mask on the wrong thing
        # is then either its mistake (wrong label) or SAM 2's (right label).
        logger.info("model selected %s", ", ".join(
            "%r at (%.2f, %.2f)" % ((t["label"] or "?")[:40], t["u"], t["v"]) for t in targets))
    return say, heard, targets


def _one_point(
    entry: object, width: int | None, height: int | None, fallback_label: str | None = None
) -> dict | None:
    """Validate one requested point. The coordinates may come back as 0..1
    fractions or as pixels of the image we sent; the model uses both despite
    the prompt, and a rejected point means that object is not tracked."""
    if not isinstance(entry, dict) or entry.get("type") != "image_point":
        return None
    coords = [entry.get("u"), entry.get("v")]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
           or n < 0 for n in coords):
        return None
    u, v = float(coords[0]), float(coords[1])
    if u > 1 or v > 1:
        if not width or not height:
            logger.info("model returned pixels (%s, %s) but the image size is unknown", u, v)
            return None
        if u > width or v > height:
            logger.info("model point (%s, %s) is outside the %dx%d image", u, v, width, height)
            return None
        logger.info("model returned pixels (%.0f, %.0f); normalised to the %dx%d image", u, v, width, height)
        u, v = u / width, v / height
    label = entry.get("label") if isinstance(entry.get("label"), str) else fallback_label
    return {"type": "image_point", "u": u, "v": v, "label": label}


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
            max_tokens=max(self.max_tokens, 1536) if self.tracking else self.max_tokens,
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
            prompt += "\nNo camera image this turn: " + ("return track:[]." if self.tracking else "add no ops.")
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
            system=(
                TRACKING_PROMPT if self.tracking
                else TOOLS_SYSTEM_PROMPT if tools_available
                else SYSTEM_PROMPT
            ),
            audio_as=self.audio_as,
        )
        started = time.monotonic()
        logger.info(
            "calling %s: %.2f s audio, %s jpeg bytes, tracking=%s",
            self.model, len(pcm) / 32000, len(jpeg) if jpeg else 0, self.tracking,
        )
        if self.tracking:
            # One multimodal call selects points or returns a bounded guide.
            try:
                text, record = await asyncio.to_thread(self._call, messages)
            except Exception as exc:
                logger.warning("%s call failed: %s: %s", self.model, type(exc).__name__, exc)
                raise
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.debug("%s raw reply: %s", self.model, text[:400].replace("\n", " "))
            say, heard, targets = parse_tracking_reply(
                text,
                envelope.get("sent_w") if envelope else None,
                envelope.get("sent_h") if envelope else None,
            )
            reply = _extract_json_object(text) or {}
            guide = None
            if "guide" in reply:
                targets = []
                if jpeg is not None and envelope:
                    try:
                        guide = GuidePlan.from_dict(reply["guide"])
                    except ValueError:
                        logger.info("dropping invalid guide plan")
                if guide is None:
                    say = "I couldn't build a clear guide from that view. Please look at the objects and try again."
                else:
                    # The controller owns first-step speech after tracker seeding.
                    say = ""
                    targets = guide.tracking_targets
            return PlanResult(ops=[], text=say, heard=heard, latency_ms=latency_ms,
                              audit_id=record.get("call_id"), guide_plan=guide,
                              tracking_targets=targets if jpeg is not None and envelope else [])
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

    async def guide_command(self, *, pcm: bytes) -> str:
        """Transcribe a guide control turn without an image, tools, or replanning."""
        from voice.audio import build_voice_messages, pcm_to_wav_bytes
        from yibu_http import chat_completion, require_api_key

        messages = build_voice_messages(
            "Transcribe the recorded speech.", wav=pcm_to_wav_bytes(pcm), jpeg=None,
            system='Return ONLY JSON {"heard":"verbatim speech transcript"}. '
                   'Do not answer the request, generate instructions, or infer words not spoken. '
                   'For silence or unintelligible audio return {"heard":""}.',
            audio_as=self.audio_as,
        )

        def call() -> str:
            text, _, _record = chat_completion(
                api_key=require_api_key(), model=self.model, messages=messages,
                purpose="guide-command", max_tokens=128,
            )
            return text

        text = await asyncio.to_thread(call)
        obj = _extract_json_object(text) or {}
        return obj["heard"] if isinstance(obj.get("heard"), str) else ""

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
