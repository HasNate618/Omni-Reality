# Omni spatial loop (slices 0-2)

What it is: the offline Quest 3S + laptop drawing loop. The headset captures
the scene and plants world-locked marks; the laptop coordinator authors scene
ops over LAN; Unity renders and ACKs. No model calls in these slices.

## Contract (short form)

- Canonical camera is **left** (`PassthroughCameraAccess`, 1280x960); world
  frame is `openxr_floor_stage` (Unity left-handed, metres, quaternions xyzw).
- Every placement uses the **capture-time** pose and ray, not the current head
  pose. Per-`frame_id` geometry is cached for 10 s (`CaptureGeometryCache`).
- A `world_hint` is one capture-time hit (`pointing` or `centre`); the 0.12 m
  stale rule compares the **same ray** only. Hits valid between 0.25 m and 4 m.
- A `mark` with no motion defaults to a pulsing ring (period 1.2 s, 0.06 m
  ring). Pulse runs at headset framerate and must stay on the surface while
  the wearer walks — never stuck to the head.
- Laptop → Quest `scene_op`s go over one WebSocket as JSON text frames, no
  TLS. Quest replies with a `PlacementAck` (`placed` / `rejected` / `stale`).
  No success is claimed before the ACK. `cancel`/`ack` send before any frame
  bytes; drawings stay on Quest if the socket dies.
- Misses are honest chips, never floating pins: no-surface "I can't plant
  that on a surface.", stale "That moved, look again.", too-close "Too close
  for depth.", socket down "Laptop not connected."
- **No yibuapi in slices 0-2.** With the default `--planner mark` the
  coordinator never calls a model or reads an API key. Only `--planner yibu`
  (slice 3, below) does, via `provider/` helpers and `YIBU_API_KEY`. Unity has
  no key path and never POSTs anywhere.

## Pointers

- Binding design: `docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`
  (§14 build order; this doc covers slices 0 contract, 1 envelope+pin, 2 LAN).
- Build plan: `docs/superpowers/plans/2026-09-19-spatial-omni-slices-0-2.md`.
- JSON Schemas (authoritative, not repeated here):
  `provider/protocol/schemas/` (`message`, `capture_envelope`, `scene_op`,
  `model_scene_op`, `placement_ack`).
- Coordinator: `provider/coordinator/server.py`, run from `provider/` with
  `python -m coordinator.server` (binds `ws://0.0.0.0:8765`).
- Unity side: `QuestDemo/Assets/Spatial/` (`SpatialRuntime`, `PlacementResolver`,
  `PulsingRing`, `HonestyChip`, `CoordinatorClient`, `CaptureGeometryCache`).

## Voice turn (slice 3, laptop side)

**Tracking integration:** `--sam2-url ws://127.0.0.1:8766` with `--planner
stub|yibu` opts into a separate single-object tracking turn. Quest's
`QuestStreamInput` sends real JPEGs and A-button push-to-talk PCM. The selected
frame and Huawei point seed the existing SAM 2 click protocol; results return
as `tracking_result` instead of spatial `scene_op`s. Follow
[Quest camera + push-to-talk setup](quest-audio-setup.md); the frame contract
lives in [omni-sam2-streaming.md](omni-sam2-streaming.md#quest--voice-automatic-initialization).
The existing placement/ACK turn below remains the default without that flag.

- Coordinator runs voice turns only with a planner:
  `python -m coordinator.server --planner stub` (offline) or `--planner yibu`
  (live `qwen3.8-omni-flash`, spends credit). Default `--planner mark` keeps
  the slice-2 hardcoded mark above.
- Quest sends `frame` (with `utterance_id`, envelope, `jpeg_b64`), ~100 ms
  `audio_chunk`s, then `utterance_end`. Under 0.5 s of PCM → no turn.
- Turn order: `turn_started` → planner (full response, WAV + JPEG) → at most
  3 schema-valid `scene_op`s (list frozen first) → wait 1500 ms for ACKs →
  one `speak` (`audio: null`). Speech claims a drawing only if every op ACKed
  `placed`/`applied`; rejected/stale use the honesty copy; a missing ACK says
  "I couldn't confirm placement." once, and a late ACK never speaks again.
- `cancel {turn_id}` tombstones the turn: no further ops or speech for it.
- Planner seam: `provider/coordinator/planner.py` (`Planner.plan` →
  `PlanResult`). Spatial prompt/parse is Member A's `spatial_ops.parse_model_reply`
  when present; a JSON-extract fallback stands in until then.
- Headset-free end-to-end: `python -m tools.fake_quest --say "mark the laptop"
  --jpeg photo.jpg` (`--reject`, `--no-ack` for honesty paths).
- Not yet: spoken follow-up after the tool result (spec §7 step 5) is the
  first reply's text, not a second tools-disabled call.

Tracking uses status/result events rather than the placement-ACK speech path.
Its Unity source does not implement speech playback or a new passthrough renderer.

## Running it (one command)

`./start-demo.sh` from the repo root starts the SAM 2 server (reusing a loaded
one), replaces any leftover coordinator, loads `provider/.env`, re-creates the
`adb reverse tcp:8765` tunnel, launches the app, and tails the interesting log
lines. `--stub` skips the model call (seeds the frame centre); `--no-app`
leaves the headset app alone. Ctrl+C stops what it started; logs in `logs/`.

Only one coordinator may run at a time: a leftover process keeps the headset's
socket through the USB tunnel and silently swallows everything it sends, which
looks exactly like a dead pipeline. The tunnel also disappears whenever adb
restarts or the cable is unplugged, so re-run the script.

## Seeing the masks in the headset

`QuestDemo/Assets/Spatial/TrackingMaskOverlay.cs` is the renderer that
`TrackingResult.cs` expects. It creates itself at startup (no scene wiring),
subscribes to `CoordinatorClient.TrackingResultReceived`, tints each
`mask_b64` into one RGBA texture, and draws it on a quad built from the
capture frame's intrinsics and pose, 1.5 m down the capture rays. The quad is
world-locked to the capture pose, so it holds still while the wearer moves; it
is a flat projection, exact along the capture ray and approximate off-axis.
It hides itself on `tracking_status` `stopped`/`error` or after 2 s with no
result. Without this component the masks arrive and are only logged
(`QUEST_TRACKING first mask ...`), which is what "nothing renders" looked like.

## How to verify

- Offline (no credit, no headset): from `provider/`,
  `python -m unittest discover -s tests -v` — schema, uv-convention, stale,
  coordinator, send-priority, and voice-turn tests.
- Headset, slice 1: native build (XR Simulator has no PCA), plant the ring on
  a real table, walk ~90 degrees — pulse stays on the table. Then force a
  miss: chip appears, nothing floats mid-air.
- LAN, slice 2: set the Quest `laptop_ipv4` to the laptop LAN address, plant
  via one laptop-authored `mark` after a `hello` — ring appears and the
  coordinator logs `ack status=placed`. Unplug Wi-Fi: rings remain, chip shows
  offline. Recordings stay gitignored (`provider/artifacts/`).
- Note: the headset and LAN pass/fail steps above are the acceptance gate for
  slices 1-2 and have **not** been run on device in this worktree; Python
  tests are the offline gate.
