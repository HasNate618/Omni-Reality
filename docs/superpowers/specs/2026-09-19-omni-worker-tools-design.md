# Omni worker tools: SAM2, generation, and scene API

Date: 2026-09-19.

Status: binding for the Omni-track worker milestone. It extends
`docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`
(parent) and
`docs/superpowers/specs/2026-09-19-voice-spatial-omni-integration-design.md`
(voice spec). Where those are silent, they still bind. Where they conflict
on Omni tools, SAM2, image-to-3D, `place_generated`, or local GPU workers,
this spec wins.

This spec does not weaken the voice spec’s keyword turn, ACK barrier,
tools-disabled final line, or required cloud PCM in `speak.audio`.
`place_procedural` / `revise_procedural` stay as defined there. This spec
adds the shop-to-life path: Omni calls local workers as tools, a ghost
lands immediately, a generated mesh auto-places later.

Hardware, live yibu calls, SAM2, and generation jobs are unrun in this
branch. §9 names what must still be proven.

## 1. Product

Hero: the wearer looks at a shop listing (or any real object), says
“bring that chair to life here,” and gets (a) a capture-time-grounded
ghost/label immediately, (b) a later GLB on that same pin, (c) cloud
spoken confirmation, then bounded “bigger / rotate / nudge” on that
drawing.

Omni is the brain: it hears the utterance, sees the JPEG, chooses the
region, calls tools, and writes truthful speech. SAM2 only refines a
region. The generator only builds a mesh. Quest only places and ACKs.
None of those three is a second assistant.

## 2. Where things run

```
Quest / Unity
  mic PCM + JPEG + CaptureEnvelope
        │ LAN WebSocket (existing control plane)
        ▼
Laptop coordinator (Python; sole control-plane authority)
  ├─ Cloud Omni HTTP adapter (tools on, then tools off)
  ├─ Cloud speech adapter (PCM after ACK / after mesh ACK)
  ├─ SAM2 worker client (loopback)
  ├─ generation worker client (loopback or overflow host)
  └─ artifact HTTP server (LAN, job_id only)
        │
        ▼
Quest / Unity
  scene_op + later fetch GLB by coordinator job_id
  → capture-time anchor → PlacementAck
```

| Process | Host | Quest may call it? |
| --- | --- | --- |
| Coordinator WebSocket `ws://<laptop>:8765` | demo laptop | yes (only control plane) |
| Cloud Omni / cloud speech | yibuapi via coordinator | no |
| SAM2 worker | demo laptop loopback | no |
| Image-to-3D worker | demo laptop loopback, or second 4060 host | no |
| Artifact HTTP | demo laptop LAN | yes, `GET /artifacts/<job_id>.glb` only |

`YIBU_API_KEY` stays on the coordinator laptop. Workers, Unity, logs, and
the repo never see it. Workers receive JPEG bytes from the coordinator,
never from Quest.

One writer per path: coordinator/schema/transport is the integration
lead; SAM2/gen workers live under a new `provider/workers/` tree;
artifact serving is coordinator-owned; Quest GLB fetch/placement is a
narrow `QuestDemo/Assets/Spatial/Generated/` factory. Collaboration
rules in `docs/hackathon-collaboration.md` apply.

## 3. Omni tool surface

Baseline reasoner remains HTTP Chat Completions on `qwen3.8-omni-flash`
with raw mic PCM (≥ 0.5 s) plus the turn JPEG plus text. Transcript-only
or image-only reasoner calls fail the Omni-track gate.

One reasoner call may run a bounded tool loop (max 4 tool rounds, then
force `ops_closed`). Tools enabled for that loop:

### 3.1 `inspect_objects`

Omni sends:

```json
{
  "frame_id": "01k...",
  "target": { "type": "image_box", "frame_id": "01k...", "u0": 0.2, "v0": 0.1, "u1": 0.8, "v1": 0.9 },
  "phrase": "chair"
}
```

`target` is required and is `image_point` or `image_box` in the **sent
JPEG**, top-left origin, `[0,1]`, same UV contract as the parent spec.
`phrase` is optional, max 40 characters, a hint only. `frame_id` must be
this utterance’s capture. Foreign or expired `frame_id` returns an error
tool result, not invented boxes.

Coordinator looks up that JPEG in the turn buffer, calls SAM2, and
returns:

```json
{
  "frame_id": "01k...",
  "count": 1,
  "objects": [
    { "object_id": "01k...", "u0": 0.31, "v0": 0.22, "u1": 0.74, "v1": 0.81, "score": 0.91 }
  ]
}
```

`object_id` is a coordinator-issued lowercase ULID. Boxes are sent-JPEG
UVs. Masks never go to Omni. On SAM2 failure or empty result:
`count: 0`, `objects: []`, plus `error` (`no_object` / `timeout` /
`busy`). Omni must not invent a box.

SAM2 does not classify “chair.” Omni chose the region; SAM2 refines it.
Default: return at most 3 objects, highest score first. Omni then picks
one `object_id` for generation.

### 3.2 `start_generation`

Omni sends either a session `object_id` from `inspect_objects`, or an
`image_box` on this turn’s `frame_id`, plus optional `prompt` (max 80
chars):

```json
{
  "object_id": "01k...",
  "prompt": "wooden dining chair"
}
```

Coordinator returns immediately:

```json
{ "job_id": "01k...", "status": "queued", "frame_id": "01k..." }
```

It does not wait for the GLB. It crops the JPEG with the stored mask or
box, enqueues the worker, and keeps `job_id` → `frame_id` + target +
utterance mapping.

Reject (tool error, no job): unknown `object_id`, foreign `frame_id`,
missing image, GPU/queue full (`busy`), or a second generation while one
job is already queued or running for this session.

### 3.3 `emit_scene_ops`

Unchanged parent/voice contract: at most 3 validated `ModelSceneOp`s,
image-space targets only, no metres, no quaternions, no C#, no URLs, no
file paths, no world points.

Omni **must not** emit `place_generated` or `place_known`. Those are
coordinator-authored after a real artifact exists. For “bring it to
life,” Omni emits a `ghost` and/or `label` on the inspected box or
`object_id`’s box (as `image_box`) so Quest plants a preview now.

Coordinator stamps `op_id`, `turn_id`, `stage_epoch` and sends `scene_op`
only after `turn_started`.

### 3.4 After tools: ACK then speech

Same voice-spec barrier: freeze ops → Quest ACKs (1500 ms) → return tool
results including placement ACKs → tools-disabled final line → cloud PCM
in `speak.audio`. Generation still running is not a failure of this
turn. Success speech may say the preview is placed and the mesh is
coming; it must not claim the GLB is in the room until §4 completes.

## 4. Job completion (auto-place, then announce)

When the worker reports `ready` and the coordinator validates the GLB:

1. Coordinator sends a coordinator-authored `scene_op` `kind:
   place_generated` with `job_id` it issued, `target` copied from the
   inspect/generation mapping (same `frame_id` and image-space box or
   point), current `stage_epoch`, new `op_id`, and a `turn_id` for this
   completion turn.
2. Quest fetches `GET http://<laptop_ipv4>:<artifact_port>/artifacts/<job_id>.glb`
   over LAN only. The `job_id` is the capability token. The model never
   supplies a URL. Laptop IPv4 is the existing player pref; artifact
   port is advertised as `hello_ok.artifact_port` (integer, default
   `8766`).
3. Quest raycasts that capture-time target with the existing 10 s cache,
   0.25–4 m range, and 0.12 m same-ray stale rule, then sits the mesh on
   that pin. ACK `placed` / `rejected` / `stale` as today. `drawing_id`
   required on `placed`.
4. Coordinator starts a **short tools-disabled** Omni call whose only
   new evidence is the placement ACK (and `job_id` / drawing label).
   Omni writes a truthful fit line. Coordinator synthesizes cloud PCM
   and sends `speak`.
5. Later “bigger / rotate / nudge” uses the voice-spec
   `revise_procedural` action table (`enlarge` / `shrink` / `rotate_cw` /
   `rotate_ccw` / `nudge` / `remove`) on any drawing this app run created
   from `place_procedural` or `place_generated`. Quest converts; Omni
   still sends no metres.

If validation, fetch, or ACK fails: keep the ghost; do not leave a
partial mesh; speak that the mesh did not finish. Never invent a
completed object. A late `ready` after `clear_session` or a newer
`stage_epoch` is dropped (`superseded`). Do not auto-retry the same
`op_id`.

GLB rules: binary glTF, Y-up preferred, coordinator records reported
bounds; Quest clamps uniform scale so the AABB fits inside a 1.0 m
sphere and rejects `invalid` if the file is missing, not glTF, or over
25 MB. Wrong scale and invented backsides are expected; the ghost is
the honest preview.

## 5. Worker loopback contracts

Workers are separate processes. The coordinator must not import CUDA
runtimes into the WebSocket process. Interface is HTTP on loopback
(overflow gen may use a configured host).

SAM2 `POST /inspect`:

- Request: `frame_id`, JPEG bytes, `target` (point or box in sent-JPEG
  UV), optional `phrase`.
- Response: boxes as in §3.1 plus a gitignored mask artifact the
  coordinator stores by `object_id` (PNG or RLE). Masks are not
  protocol fields to Omni or Quest.

Generation `POST /jobs`:

- Request: coordinator `job_id`, cropped JPEG, optional mask, prompt,
  `frame_id`.
- Response: `{status:"queued"}`. Poll `GET /jobs/<job_id>` → `queued` |
  `running` | `ready` | `failed`. On `ready`, the worker has written the
  GLB where the artifact server can read it.

Timeouts: inspect 8 s → `timeout` tool error. Generation 90 s → `failed`;
completion path of §4 runs the failure announce. At most one generation
job queued or running per session on a given GPU.

Cancel: `clear_session`, tombstone of the originating utterance, or
socket drop cancels the job and deletes transient JPEG/mask/GLB for that
`job_id`.

## 6. GPU scheduling

Default demo machine has **one** 8GB 4060. Serialize: finish inspect
(and unload SAM2 if VRAM requires it) before loading the generator.
Never co-load SAM2 and the generator on the same 8GB card. Speech, ACK,
ghost animation, and the first-turn `speak` never wait on VRAM loads.

Overflow: a second machine with another 4060 may run the generation
worker only. Coordinator config is `SAM2_WORKER_URL` (default
`http://127.0.0.1:8771`) and `GEN_WORKER_URL` (default
`http://127.0.0.1:8772`). Do not hard-code GPU indices. If gen is
`busy`, Omni’s `start_generation` gets `busy` and should keep the
preview and say the mesh is delayed.

Local GPUs are not a live-path dependency for marks, labels, ghosts, or
voice. If SAM2/gen are down, Omni can still `emit_scene_ops` and speak;
the shop-to-life mesh scenario fails honestly.

## 7. Errors and privacy

| Failure | Scene | Speech |
| --- | --- | --- |
| Inspect empty / timeout | no fake box | ask to look closer or point |
| `start_generation` busy/invalid | ghost/label only if already ACKed | mesh not started |
| Gen timeout / invalid GLB | ghost stays | mesh did not finish |
| Quest fetch fail / `stale` / `rejected` | no mesh; ghost stays | truthful ACK language |
| yibu reasoner fail | no new ops | one honest error line |
| Cloud speech fail | drawings unchanged | caption + error chip; voice gate fails |

No continuous recording. Coordinator keeps per-turn JPEG/PCM and
per-`object_id` masks / per-`job_id` GLBs under gitignored paths, cleared
on `clear_session`, cancel, error, and drop. Do not commit JPEG, WAV,
PCM, masks, transcripts, or GLBs. Audit ledger: purpose labels, tokens,
latency, endpoints, redacted errors; no media, prompts, or reply bodies.

## 8. Schema and transport additions

Python schemas remain source of truth.

- Tool argument schemas for `inspect_objects` and `start_generation`
  (this spec). Unknown fields stripped; invalid calls are tool errors.
- `hello_ok.artifact_port` integer (default 8766).
- Coordinator-to-Quest `scene_op` for `place_generated` **requires**
  `job_id` (lowercase ULID this session issued) and an image-space
  `target` plus `frame_id`. `ModelSceneOp` from Omni still must not
  include `job_id`, URLs, or `place_generated`.
- Quest `GET` artifact by `job_id` only. HTTP 404 → ACK `rejected` /
  `invalid`.

`yibu_http.chat_completion` must accept a tools payload so this loop is
real function calling, not JSON-in-text.

## 9. Verification

Offline (no credit, no GPU required): fake SAM2 and fake gen workers;
fixtures for inspect/start_generation success, empty, timeout, busy;
Omni cannot emit `place_generated`; coordinator `place_generated` with
unknown `job_id` is dropped; ACK barrier still blocks first-turn
`speak`; completion announce is tools-disabled; artifact path contains
only ULID job ids; audit redaction tests.

Credit smoke: one HTTP Omni call with PCM + JPEG + `tools` that produces
an `inspect_objects` or `emit_scene_ops` tool call (`purpose` labeled).
Cloud-audio smoke unchanged from the voice spec.

Hardware shop-to-life: spoken request while viewing a product image
yields a grounded ghost/label, ledger shows audio bytes on the reasoner
call, later a mesh on the same pin while the wearer walks ~90°, then
cloud speech that does not claim success before the mesh ACK. Forced
SAM2-down and gen-timeout runs keep the ghost and tell the truth.

Out of scope: Quest talking to workers, model-supplied URLs/paths/C# /
world coordinates, object tracking, photoreal calipers, co-loading two
heavy models on one 8GB card, treating local TTS as the voice pass,
JSON-in-text as a passing Omni-track reasoner.

Residual: exact shop image / table object, whether SAM2 fits the demo
4060 without unload, overflow host IP for the night, Horizon OS version
in `hello`.
