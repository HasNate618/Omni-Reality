# Spatial omni assistant: design spec

Date: 2026-09-19.

Status: post-review contract. GPT-6 Astra (primary) and Muse Spark 1.3 reviewed v1. Amended for world-locked motion in v0 and later optional 3D gen / grounding on either 4060. Not an implementation plan. Demo props are not locked.

Related: `docs/omni-live-research.md` (sources), `docs/omni-live-pitch.md` (teammate pitch), `provider/README.md` (yibuapi registry).

### Integration amendment: opt-in voice-seeded SAM 2 (2026-09-19)

The next integration adds a single-object tracking mode alongside these spatial
drawing slices. Quest streams JPEGs (default 3 fps) and A-button push-to-talk
audio to the coordinator. One immutable selected JPEG + audio goes to Huawei;
its interior image point initializes the existing SAM 2 click path on that
exact frame. A bounded JPEG history supplies successor frames, then live
tracking continues. The SAM 2 worker returns frame-addressed masks to Unity's
existing-consumer hook; this amendment does not specify a new renderer.

This mode is enabled by the Unity tracking settings asset and coordinator
`--sam2-url`. It is an explicit exception to the event-driven 2 fps capture
limit and object-tracking non-goal below. It uses tracking status/results
instead of pretending a segmentation initialization is a world-placement ACK.
The original spatial-mark mode retains its contract. Tracking wire rules,
limits, and verification results live in `docs/omni-sam2-streaming.md`.

### Amendment: multi-object targeting (2026-09-20)

The tracking mode above is no longer single-object. One utterance selects up to
3 objects, all clicked on the one selected frame so a single SAM 2 session holds
them together, and each is drawn in the headset with its own colour, its own
measured depth, and the model's name for it. The cap is a frame-budget limit,
not a model limit: each object costs roughly +70 ms per tracked frame, so three
fit the 333 ms that the default 3 fps stream allows and four do not. Everything
else above stands — one Huawei call per utterance, the immutable selected JPEG,
the bounded history and replay, and status/result events rather than placement
ACKs. A new utterance replaces the whole selection, not one object of it.
Measured timings and the wire contract stay in `docs/omni-sam2-streaming.md`.
The setup walkthrough lives in `docs/quest-audio-setup.md`.

## 1. Product

A Quest 3S assistant that sees a selected view, hears speech, talks back, and can put its answer in the room as world-locked geometry.

The model decides *what* to show. The headset decides *where* it can safely go. The model never emits world coordinates or C#.

Drawings that stick to real surfaces come first. Those drawings move: a pin can pulse, a path can draw on, a ghost can rotate or slide on the object. Unity owns the animation loop at headset framerate. The model requests a motion kind; it does not emit keyframes or C#.

Spawned meshes (known library first, generated later) are optional and off the live speech path. Short working context exists so "that / the other one / what I just said" work. Memory is not the pitch.

Non-goals for this version:

- A specialist app (electrician trainer, kitchen coach, furniture placer). Those are uses of the same harness.
  - **Amended 2026-09-20.** Layout mode (`docs/omni-layout-mode.md`) is exactly such a use, and it shipped.
    It is built on this harness -- the same transport, scene ops, ACK barrier, and honesty discipline -- and
    adds one op kind. The non-goal stands as written for the *harness*: this spec still does not describe a
    furniture app. It no longer means no such mode may exist.
- Photoreal text-to-3D on the critical path. A later illustrative mesh is allowed; it is not a caliper.
- Object-following pins that track a moving mug or cable.
- Always-on labels on every object.
- Claiming millimetre fit, fastener size, live/dead electrical state, food temperature, or structural load.
- Arbitrary code execution from the model.

The judged interaction is whatever table object we have that night. The spec does not depend on a bike, a panel, or a lamp.

## 2. System shape

```
Quest 3S (Unity C#)                    Laptop (Python)
──────────────────                    ────────────────
Camera, mic, pointing                 Coordinator process
CaptureEnvelope                       Session, turns, tools, cancel
Depth / MRUK raycast                  Cloud omni via provider/
Render, occlusion, animation          Optional workers on either 4060
Speech playback                       (grounder and/or 3D gen, not both on one card)
Placement ACK  <── WebSocket JSON ──>  Speak(text) adapter
```

Rendering never waits on the network to keep an already-placed drawing stable.

`QuestDemo/` is the Unity app. `provider/` owns every yibuapi call. The API key stays in `YIBU_API_KEY` on the laptop. It never enters Unity, logs, or the repo.

Languages: C# on device, Python on the laptop. Two RTX 4060s are two 8GB budgets, not 16GB pooled. Either card may later run a grounder or a 3D generator. One resident heavy model per card. Never co-load a grounder and a generator on the same 8GB. If only one 8GB card is present, skip 3D gen; keep at most one optional worker.

Sponsored key expires 2026-09-20 12:00 UTC (8:00 AM EDT). Live yibu calls must stop before that. After expiry, the headset still shows existing drawings and `honesty: offline`.

## 3. Identifiers and clocks

All IDs are lowercase ULIDs unless noted.

| ID | Owner | Lifetime |
| --- | --- | --- |
| `session_id` | Coordinator, in `hello_ok` | Until disconnect. New handshake gets a new id. |
| `utterance_id` | Quest, when a talk gesture or VAD open starts | That utterance (audio + frames + `utterance_end`) |
| `turn_id` | Coordinator, monotonic uint64 starting at 1 | One utterance through `ops_closed`, ACKs, and spoken follow-up |
| `frame_id` | Quest, at capture | Until Quest drops capture geometry (10 s after capture) |
| `stage_epoch` | Quest, incremented on guardian / XR origin reset | Until next reset |
| `target_id` | Coordinator, after Quest resolves a spatial target | Until `clear_session` or app quit |
| `drawing_id` | Quest, when an op is accepted | Until remove / undo / `clear_session` / app quit |
| `op_id` | Coordinator, per scene op | That request. Duplicate `op_id` is ignored. |

`session_id` is the conversation id on the laptop. Drawings live on Quest. A dropped socket does not delete drawings. A new `hello_ok` does not delete drawings. The only remote wipe is acknowledged `clear_session`. After reconnect, the coordinator starts a new `session_id` with empty working context. Drawings that remain on Quest are visible but not addressable by `drawing_id` until the wearer clears or the app reports them (v0 does not send a snapshot).

Time:

- Quest stamps capture, pointing, and audio with headset wall clock as `t_unix_ns`.
- Laptop stamps receipts and audit with laptop wall clock.
- v0 does not NTP-sync. On `hello`, both sides log their clocks. If they differ by more than 2 s, record `clock_skew_ns` and keep running. Do not rewrite capture poses.
- Bind pointing to frames using **Quest timestamps only**. Laptop time is audit-only.

Canonical passthrough camera is **left**.

## 4. Coordinate contract

This is the freeze Unity and Python must share. Do not invent a second convention.

World frame name: `openxr_floor_stage`.

- Origin on the floor, as Unity XR Origin reports it.
- Left-handed (Unity).
- +X right, +Y up, +Z forward in the tracking space.
- Positions in metres.
- Rotations are unit quaternions in `xyzw` order.
- `pose` on the envelope is **left-camera to stage**: a point in camera space `p_cam` maps to stage as `R(q) * p_cam + t`.
- After a guardian or XR origin reset, Quest increments `stage_epoch` and includes it on every envelope and `scene_op` ACK. Coordinator discards ops and envelopes whose `stage_epoch` is older than the latest Quest `hello` / envelope.

Image:

- Pixel origin is the **top-left** of the image.
- `image_w` / `image_h` are the **original** camera texture size from PCA (not necessarily 1280x960).
- `sent_w` / `sent_h` are the JPEG that actually went over the wire.
- `intrinsics` (`fx`, `fy`, `cx`, `cy`) are in **original camera pixels**.
- `distortion` is whatever PCA reports for that frame (`model` plus `k`). Do not hard-code `"none"`. If the SDK gives no distortion, send `{"model":"none","k":[]}`.
- `crop` maps a pixel in the sent JPEG back to the original camera image: `u_cam = sx * u_jpg + tx`, same for `v`. Identity if uncropped.
- Normalized `u`,`v` in targets are in the **sent JPEG**, in `[0,1]`, and refer to the **pixel centre**: `u_jpg = u * sent_w - 0.5`, then apply `crop`.

`pose.t_unix_ns` (on the envelope) is the exposure-matched camera pose time from the SDK, not the time the JSON was serialized.

## 5. Capture envelope

Every image that leaves the headset carries this object. Delayed work must use this pose, not the wearer's current head pose.

```json
{
  "frame_id": "01k...",
  "stage_epoch": 1,
  "t_unix_ns": 0,
  "camera": "left",
  "image_w": 1280,
  "image_h": 960,
  "sent_w": 1280,
  "sent_h": 960,
  "intrinsics": { "fx": 0, "fy": 0, "cx": 0, "cy": 0 },
  "distortion": { "model": "none", "k": [] },
  "pose": {
    "px": 0, "py": 0, "pz": 0,
    "qx": 0, "qy": 0, "qz": 0, "qw": 1,
    "frame": "openxr_floor_stage"
  },
  "crop": { "sx": 1, "sy": 1, "tx": 0, "ty": 0 },
  "pointing": null,
  "world_hint": null,
  "capture_geometry_available": false
}
```

`pointing`, when present:

```json
{
  "source": "head",
  "t_unix_ns": 0,
  "origin": { "px": 0, "py": 0, "pz": 0 },
  "direction": { "x": 0, "y": 0, "z": 1 },
  "frame": "openxr_floor_stage"
}
```

`source` is `head`, `controller`, or `hand`. Head reticle is not eye gaze. Bind pointing to the envelope whose Quest `t_unix_ns` is nearest and not later than the pointing timestamp.

`world_hint`, when present, is a capture-time surface hit for **one** ray Quest actually cast at capture (pointing if present, otherwise image centre):

```json
{
  "px": 0, "py": 0, "pz": 0,
  "nx": 0, "ny": 1, "nz": 0,
  "frame": "openxr_floor_stage",
  "source": "pointing",
  "t_unix_ns": 0
}
```

`source` is required when `world_hint` is non-null: `pointing` or `centre`. A centre hint does **not** validate a later `image_point` on a different pixel.

Quest also keeps a 10-second per-`frame_id` capture-geometry cache: pose, intrinsics, crop, and any depth/mesh hit it computed at capture. `capture_geometry_available` is true when that cache still exists.

JPEG: use the SDK resolution (1280x960, or 1280x1280 on Horizon OS v83 if available), quality 70, max 350 KB `jpeg_b64`. If over cap, downscale and update `crop` / `sent_*`. Do not send every frame. Send on (a) start of a user utterance, (b) a pointing event, (c) coordinator `request_frame` after a rejected placement. Cap at 2 fps to the laptop.

## 6. Transport

One WebSocket, Quest client to laptop server, JSON text frames.

Laptop binds `ws://0.0.0.0:8765` on the demo LAN. Quest stores the laptop IPv4 in a player pref, not a secret. No TLS in v0 (local network only). Unity does not HTTP POST to yibuapi.

Message envelope:

```json
{
  "v": 1,
  "type": "hello",
  "session_id": null,
  "turn_id": 0,
  "utterance_id": null,
  "payload": {}
}
```

`session_id` is null only on the first `hello` before `hello_ok`. After that, Quest echoes the assigned id. `turn_id` is 0 until `turn_started`.

Quest to laptop:

| `type` | payload |
| --- | --- |
| `hello` | `{ "device": "quest3s", "app": "QuestDemo", "os_version": "74", "capabilities": { "pca": true, "depth": true, "tts": true } }` |
| `audio_chunk` | `{ "utterance_id", "t_unix_ns", "audio": { "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1, "data_b64": "..." } }` |
| `utterance_end` | `{ "utterance_id", "t_unix_ns" }` |
| `frame` | `{ "utterance_id", "envelope": CaptureEnvelope, "jpeg_b64": "..." }` |
| `ack` | PlacementAck |
| `cancel` | `{ "turn_id", "reason": "barge_in" }` |
| `clear_session` | `{ "reason": "user" }` |
| `ping` | `{ "t_unix_ns" }` |

Laptop to Quest:

| `type` | payload |
| --- | --- |
| `hello_ok` | `{ "session_id", "laptop_t_unix_ns" }` |
| `turn_started` | `{ "utterance_id", "turn_id" }` |
| `scene_op` | SceneOp |
| `speak` | `{ "turn_id", "text", "audio": null }` |
| `stop_speak` | `{ "turn_id" }` |
| `request_frame` | `{ "reason": "reobserve" }` |
| `honesty` | `{ "state", "text" }` |
| `clear_session` | `{ "reason": "coordinator" }` |
| `session_cleared` | `{ "session_id", "generation": 1 }` |
| `pong` | `{ "t_unix_ns" }` |

`speak.audio` when present is `{ "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1, "data_b64": "..." }`. v0 default is `audio: null`: Quest speaks `text` with on-device TTS and may show the same line as a caption. Captions without TTS are degraded mode, not proof of voice.

Send priority, high to low: `cancel`, `stop_speak`, `ack`, `turn_started`, `scene_op`, `speak`, `honesty`, `clear_session`, `pong`/`ping`, `audio_chunk`, `frame`. Do not block `cancel` / `stop_speak` / `ack` behind a JPEG.

Audio: 16 kHz mono s16le. Batch about 100 ms per `audio_chunk` (not 20 ms JSON). Quest may VAD locally and only stream while an utterance is open; if VAD is not ready, stream while the wearer holds a controller trigger labeled talk.

Reconnect: Quest retries every 2 s. On success, new `session_id` via `hello_ok`. Coordinator does not replay old turns. Drawings stay on Quest until `clear_session` or app quit.

`clear_session` is acknowledged. Quest clears drawings, coordinator drops audio/frame/transcript buffers, both increment a generation fence. In-flight ops for the old generation are `superseded`.

## 7. Turns, ACK, interrupt

A talk gesture or VAD open creates `utterance_id` on Quest. Audio chunks and frames for that speech carry it. `utterance_end` closes it.

The coordinator assigns `turn_id` and sends `turn_started{utterance_id, turn_id}` before any `scene_op` or `speak` for that utterance. Quest does not invent `turn_id`.

HTTP path (default):

1. Coordinator has **mic PCM** (at least 0.5 s) and usually a JPEG for that utterance. Transcript-only, with no audio bytes in the yibu call, is not a passing slice-3 turn.
2. Coordinator waits for the **complete** HTTP response. It does not speak on a timer while the model is still generating.
3. It freezes at most 3 ops, assigns `op_id`s, sets `ops_closed` for that `turn_id`, and sends those `scene_op`s.
4. Quest validates and renders, then sends `ack` per `op_id` (target: within 500 ms of render-or-reject).
5. Coordinator waits for a terminal ACK on every frozen `op_id` (1500 ms). Then it returns the tool result to the model with **further tools disabled** and requests the spoken follow-up.
6. Only then may it send `speak` for that `turn_id`.
7. If any op is `rejected` or `stale`, spoken text must not claim it was marked. The coordinator may `request_frame` and wait for a new utterance.

Zero-op turns: if the closed HTTP response contains no ops, send `speak` after `ops_closed` with an empty op list. There is no 800 ms "maybe there are no ops" window.

If an ACK never arrives: send no success line. Speak once: "I couldn't confirm placement." Do not retry the same `op_id`. A late ACK for that `op_id` updates Quest state only; it does not trigger another `speak`.

`PlacementAck`:

```json
{
  "op_id": "01k...",
  "turn_id": 3,
  "stage_epoch": 1,
  "drawing_id": "01k...",
  "status": "placed",
  "reason": null,
  "pin": "surface"
}
```

`status` is `placed`, `applied`, `rejected`, or `stale`.

- `placed`: a new or replaced drawing is on screen. `drawing_id` required. `pin` is `surface`.
- `applied`: `remove` / `undo` succeeded. `drawing_id` is the removed id, or null if undo had nothing.
- `rejected` / `stale`: `drawing_id` is null. `reason` required.

`reason` when not placed: `out_of_camera`, `too_small`, `too_close`, `no_surface`, `clutter`, `invalid`, `superseded`, `timeout`.

Interrupt:

- Quest stops local playback immediately on barge-in and sends `cancel` for the **current** `turn_id` (or `utterance_id` if `turn_started` has not arrived).
- Coordinator writes a tombstone for that `turn_id` / `utterance_id`. It drops late model output, queued TTS, and further `scene_op` / `speak` for that id.
- Quest drops `scene_op` and `speak` whose `turn_id` is **equal to a tombstoned turn or less than the latest active turn**. Already `placed` drawings stay unless a new op revises or removes them.
- Last writer wins per `drawing_id`. A new op that names an existing `drawing_id` replaces it.

## 8. Scene ops v0

Two shapes. Mixing them is a bug.

**ModelSceneOp** is what `emit_scene_ops` accepts. No metres, no quaternions, no `world_point`.

**SceneOp** is what the coordinator sends to Quest after filling `op_id`, `turn_id`, and `stage_epoch`. Quest may attach a resolved `world_point` that **it** computed. The coordinator must not copy model-supplied coordinates into that field.

Unknown `kind` is `rejected` / `invalid`. Unknown target type is `invalid`. Coordinator strips unknown fields from ModelSceneOp.

Shared SceneOp fields:

```json
{
  "op_id": "01k...",
  "turn_id": 3,
  "stage_epoch": 1,
  "kind": "mark",
  "drawing_id": null,
  "target": { "type": "image_point", "frame_id": "01k...", "u": 0.42, "v": 0.51 },
  "style": { "color": "#3DDCFF", "label": null },
  "motion": { "kind": "pulse", "period_s": 1.2 }
}
```

`motion` is Unity-owned looping animation on that drawing. It runs until remove, undo, revise, or `clear_session`. Barge-in does not freeze already-placed motion unless a new op revises that `drawing_id`. Omit `motion` on a new `mark` to get `pulse` at 1.2 s. Send `"motion": null` only when a pin must stay still. `ghost` without `rotate` or `slide` is `invalid`.

```json
{
  "kind": "pulse",
  "period_s": 1.2
}
```

Motion kinds:

| `motion.kind` | Extra fields | Unity does |
| --- | --- | --- |
| `pulse` | `period_s` 0.6..3 | Opacity/scale loop on a mark or label. Default on a new `mark` if `motion` is omitted: `pulse` at 1.2 s so pins are not dead stickers. |
| `travel` | `period_s` 1..4 | A tick moves along a `connect` polyline. |
| `rotate` | `axis` `x`/`y`/`z` in the hit tangent frame, `angle_deg` 15..180, `period_s` 1..4 | Ghost arc around the real pivot. Default `axis` is `y` (surface normal). |
| `slide` | `axis` `x`/`z` in the tangent plane, `distance_m` 0.03..0.4, `period_s` 1..4 | Ghost slides in and out of a slot. |

Tangent frame at the hit: +Y is the surface normal; +Z is the projection of world +Z onto the tangent plane, or world +X if that is degenerate. Slice 5 may tighten this; v0 uses this default so `rotate`/`slide` are implementable without waiting.

Model target types:

| `target.type` | Fields | Use |
| --- | --- | --- |
| `capture_hint` | `frame_id` | Use that frame's pointing, else `world_hint`. Headset decides the point. |
| `image_point` | `frame_id`, `u`, `v` | Pixel in the sent JPEG. Quest unprojects with that frame's capture pose. |
| `image_box` | `frame_id`, `u0`, `v0`, `u1`, `v1` | Ring around the box centre. Bounds feed `too_small`. |
| `drawing` | `drawing_id` | Revise or remove an existing drawing. |
| `pointing` | `frame_id` | Use `envelope.pointing` for that frame. |

Quest-only target type (never from the model):

| `target.type` | Fields | Use |
| --- | --- | --- |
| `world_point` | `px, py, pz, frame`, optional `nx,ny,nz`, `frame_id` | Capture-time hit Quest computed for this op. |

Kinds:

| `kind` | Extra fields | Unity does |
| --- | --- | --- |
| `mark` | `shape`: `ring` (default) or `dot`; optional `motion` | Surface pin at the resolved hit. Diameter 0.06 m default, clamp 0.03..0.25 m. Default motion: `pulse`. |
| `label` | `text` max 48 chars; optional `motion` | Billboards 0.08 m above the pin. Motion default: none. |
| `connect` | `from` target, `to` target; optional `motion` | Polyline on surfaces between two hits. Max 4 m. Optional `travel`. Slice 5 defines denser sampling. |
| `ghost` | `motion` required: `rotate` or `slide` | Illustrative moving stand-in at the hit. Not a reconstruction. Same surface-pin rules as `mark`. |
| `place_known` | `asset_id`, `scale` 0.1..2; optional `motion` | Load a bundled GLB onto the hit surface. Own job id. Does not block `speak`. Disabled until a mesh exists. |
| `place_generated` | `job_id` assigned by coordinator | Later: sit an asynchronously generated mesh on the same pin. Same ACK rules as `place_known`. Disabled in slices 0-5. |
| `remove` | target `drawing` | Delete that drawing. ACK `applied`. |
| `undo` | none | Remove the most recently placed drawing in this app run. ACK `applied`. |

Validation before render:

- Hit a surface with MRUK environment raycast or depth raycast, max 4 m, min 0.25 m (Depth API is unreliable closer than about 0.2 m).
- Reject if the pixel is outside the camera image after crop (`out_of_camera`).
- `too_small` applies only when the model supplied an `image_box` whose projected width or height in the **source** image is under 8 px. Do not test the rendered 6 cm ring. `image_point` / `capture_hint` / `pointing` do not use this reason.
- At most 8 drawings on screen. A ninth `mark`/`label`/`connect`/`ghost`/`place_known`/`place_generated` is `clutter` unless it revises an existing `drawing_id`.
- `place_known` unknown `asset_id` is `invalid`. v0 bundled assets: none required.
- `place_generated` in slices 0-5 is `invalid`.
- Do not execute model-supplied C#, URLs, or file paths.

Placement and stale:

- `surface` pin: XR anchor at the resolved world point. Lives until remove / undo / `clear_session` / app quit. Does not follow a loose object.
- For `capture_hint` / `pointing`: use `world_hint` if `source` matches that ray and capture geometry is still available.
- For `image_point` / `image_box` centre: unproject with **that frame's** capture pose. If the per-frame cache still has a hit for **that same ray**, use it. Do not compare that hit to a centre/pointing `world_hint` from a different ray.
- Do not take a capture-time ray and confirm it with a *current* depth sample along a new head pose.
- If capture geometry for that ray is gone, and the only option is a delayed raycast from the old pose into the current mesh: if that hit is more than 0.12 m from the cached capture-time hit for **the same ray**, or there was no cached hit for that ray, status is `stale`. The 0.12 m figure is an engineering default, not a measured tracker accuracy.

Glue owns JSON Schema files and a handful of valid/invalid fixtures before slice 2. Conversation and Quest must consume the same schemas.

## 9. Honesty states

Quest or coordinator may send `honesty`. Unity shows a short world-locked chip near the reticle, not a floating HUD in the middle of the view.

| `state` | Spoken / chip |
| --- | --- |
| `in_camera` | (none) |
| `out_of_view` | "That's outside the camera." |
| `too_small` | "I need you closer." |
| `too_close` | "Too close for depth." |
| `no_surface` | "I can't plant that on a surface." |
| `stale` | "That moved, look again." |
| `offline` | "Laptop not connected." |

Never invent a pin in space to hide a miss. An off-screen direction chevron is optional after slice 4. It is not a pin.

## 10. Omni and speech

Coordinator is the only process that calls yibuapi, through `provider/` helpers, with `--purpose` / audit labels on every call.

Default understanding model: **`qwen3.8-omni-flash`** over HTTP Chat Completions (`https://yibuapi.com/v1/chat/completions`). Multimodal in, text out. Tools are how it draws.

Every live understanding call includes:

- text (transcript or empty)
- image JPEG when a frame was captured for that utterance
- **raw mic PCM** (or the gateway's documented audio field built from that PCM), at least 0.5 s

Default speech-out: coordinator sends `speak` with `text` and `audio: null`. Quest TTS / caption. Drawing does not wait on voice quality. Coordinator may later attach PCM in `speak.audio` from a yibu realtime model without changing the message type.

Gated upgrade: `qwen3.5-omni-plus-realtime` at `wss://yibuapi.com/v1/realtime` is on this key. Use it for live speech only after a smoke that proves (1) audio in and out, (2) image, or we still send HTTP frames for vision, (3) tool/function calls, or we keep HTTP for tools. Until that smoke passes, HTTP plus on-device TTS is the path we can demo.

Realtime rule (also in `AGENTS.md`): a tool-call turn returns arguments with no audio. Run the tool, ACK, return the tool result, then request the spoken follow-up with tools disabled.

Tool the model is allowed:

```
name: emit_scene_ops
description: Request world-locked drawings, including motion (pulse, travel, rotate, slide). Unity will ACK. Do not claim success in text until the tool result arrives. Targets are capture_hint, image_point, image_box, drawing, or pointing. Never send world coordinates.
parameters: { "ops": [ ModelSceneOp ] }
```

Coordinator fills `op_id`, `turn_id`, `stage_epoch`. Max 3 ops per turn.

System prompt constraints (short):

- You cannot move matter. You request drawings.
- Prefer `mark` / `label` / `connect` / `ghost`. Use `ghost` with `rotate` or `slide` when the user asks how something moves. Do not request `place_known` or `place_generated` unless the user asks to put an object there.
- If you cannot tell which target, ask for a point or a closer look. Do not guess safety-critical facts (live power, load ratings, doneness).
- After a tool result `rejected`/`stale`, say that plainly.

Working context: last 8 turns of transcript, last 4 `target_id`s with short labels, last 8 `drawing_id`s. No disk persistence across app restarts.

Gemini Live is registered on the key but is not the OMNI-track default. Do not send the yibu key to a non-yibu host.

## 11. Local GPUs

Slices 1 to 5 skip local models. The omni model returns an image point or capture hint; Quest unprojects. Cloud holds the assistant.

Later workers, on **either** 4060:

| Job | When it is useful | Candidate | Constraints |
| --- | --- | --- | --- |
| Phrase grounder | Pointing cannot tell "the other pad" / "the worn one" among lookalikes | Small open-vocab detector first (Grounding DINO or YOLO-World). NVIDIA LocateAnything-3B only after license check | Query-time, not every frame. Academic/non-profit license on LocateAnything; commercial use not permitted except NVIDIA. 8GB fit and 4060 latency unverified. Published numbers are on large GPUs. |
| Known mesh | User asks to put a specific library object on the table | Bundled GLB via `place_known` | No GPU required if the mesh ships in the app. |
| Generated mesh | Library has nothing; user still wants a stand-in | Image-to-3D (TripoSR ~6GB default in its repo) after a still of a reference, or a later text-to-image then image-to-3D step. Not text-to-3D by itself | Off the speech path. Illustrative. Wrong scale and invented backsides. TRELLIS listed 16GB in the original repo; do not plan it on 8GB. |

Scheduler rules:

- At most one resident heavy model per 8GB card.
- Never co-load a grounder and a 3D generator on the same card.
- Either card may take gen or grounding; assign the idle one. Do not hard-wire "GPU1 = finder, GPU2 = gen" in code.
- Speech, ACK, and already-playing ghost animation never wait on these jobs.
- One-card machine: grounder or gen, not both, and not on the live path.

LocateAnything is a finder, not a 3D generator. It answers "where is the thing I named in this picture." Skip it until legal is checked and a small detector has been tried. Do not make the demo depend on it.

## 12. Quest app

Native Android build on Quest 3S. Horizon OS v74+ (v83 if we want 1280x1280). Permission `horizonos.permission.HEADSET_CAMERA`. MRUK `PassthroughCameraAccess` plus Depth API plus environment raycast.

`QuestDemo` today is an OpenXR stub with a cube at `(0,1,2)`. That is not the harness.

Keep overlays and their motion in the rendering loop at headset framerate. Immediate reticle feedback is local. Model latency may only add or revise drawings. A placed `ghost` or `pulse` keeps looping without another model call.

Link is fine for iteration. Demo on the device. XR Simulator does not support this camera API.

`hello.capabilities` must reflect whether PCA, depth, and on-device TTS actually initialized. Slice 1 does not start until PCA plus a capture-time pose plus a surface hit work on the borrowed headset.

## 13. Privacy and failure

Session recordings stay on the laptop under gitignored paths. Do not commit JPEG, WAV, or transcripts. Pause / clear is a Quest button that sends `clear_session`.

If the WebSocket dies: Quest keeps drawings, shows `offline`, swallows new utterances except a local "not connected."

If yibuapi fails: coordinator sends `speak` once with a short error, audit-logs the failure, does not claim a drawing. Missing token counts stay `null`.

## 14. Build order (cool path first)

Do not wait on a flagship prop. Each slice should be playable. Glue schemas and a headset camera/depth smoke happen **before** slice 1.

0. **Contract.** This spec plus JSON Schema fixtures. No lane writes protocol by feel.
1. **Envelope + pin.** Capture left camera, log CaptureEnvelope (including SDK resolution and `stage_epoch`), plant a **pulsing** ring from pointing or a **known** image point using capture-time pose. Walk around it. The pulse must stay on the table, not on the head. Force one stale/no-surface miss and confirm we do not float junk.
2. **LAN.** WebSocket `hello` / `hello_ok`, ping, one laptop-authored `scene_op` `mark` with no model. ACK back. Prioritize `cancel` over a large frame.
3. **Omni mark.** Utterance PCM + JPEG to `qwen3.8-omni-flash` with `emit_scene_ops`. Place, ACK, tool result, then speak or caption. Pass/fail includes measured audio bytes on the HTTP call, not transcript-only.
4. **Revise.** Barge-in cancel, tombstone, new turn, last-writer-wins on `drawing_id`. An in-flight op for the cancelled `turn_id` must not appear. "The other one" must not resurrect the old op. Revising motion (faster / other way) replaces `motion` on the same `drawing_id`.
5. **Connect / ghost.** Same harness. `travel` on a path; `ghost` with `rotate` or `slide`. Tangent frame as defined above.
6. **Optional.** Grounder on an idle 4060; `place_known`; `place_generated` on the other idle 4060; realtime speech upgrade.

Pass/fail for slice 1: the pulsing ring stays on the table while the wearer walks 90 degrees. Fail if it sticks to the head, jumps when the cloud is slow, or is a frozen unmarked sticker with no motion.

Pass/fail for slice 3: one spoken request produces one grounded drawing without claiming success before ACK, and the yibu request contained mic audio.

Pass/fail for slice 4: interrupting mid-sentence stops audio and the next drawing replaces the old one. A late packet from the cancelled turn does not redraw.

Pass/fail for slice 5: a `ghost` rotation stays planted while the wearer walks; "other way / slower" revises the same drawing.

## 15. Four-person split

Agree on this spec before lanes diverge.

| Lane | Owns |
| --- | --- |
| Quest | PCA, envelope, raycast, anchors, render, ACK, playback, talk gesture, capture-geometry cache |
| Conversation | Coordinator, yibu calls, tools, TTS adapter, tombstones, `ops_closed`, audit |
| Perception | Optional grounder and/or 3D gen, GPU scheduling (after slice 5) |
| Glue | JSON schemas, fixtures, validation numbers, `clear_session`, demo object if any |

## 16. Open but not blocking

- Exact table object for judging.
- Whether plus-realtime can replace HTTP plus TTS.
- Grounder choice after a small detector trial; LocateAnything only if license covers this submission.
- Which 4060 runs gen vs grounding on a given night (scheduler, not a product choice).
- Horizon OS version on the borrowed headset (logged in `hello`).
- Confirm two 4060s vs one-card fallback on the demo machine.

Those can stay undecided while slices 0 to 5 ship. Generated meshes are not required for a complete Huawei loop.
