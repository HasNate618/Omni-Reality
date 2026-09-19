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
- SAM2/generation tool loop (inspect, async mesh, artifact GET) is documented in
  `docs/omni-worker-tools.md` and is separate from the default mark-only path.

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
  `PulsingRing`, `HonestyChip`, `CoordinatorClient`, `CaptureGeometryCache`,
  `GhostLabelConnect`, `Procedural/ProceduralFactory`, `Generated/`),
  `QuestDemo/Assets/Voice/` (`SpeakCloudPlayer`).

## Voice turn (slice 3, laptop side)

- Coordinator runs voice turns only with a planner:
  `python -m coordinator.server --planner stub` (offline) or `--planner yibu`
  (live `qwen3.8-omni-flash`, spends credit). Default `--planner mark` keeps
  the slice-2 hardcoded mark above.
- Quest sends `frame` (with `utterance_id`, envelope, `jpeg_b64`), ~100 ms
  `audio_chunk`s, then `utterance_end`. Under 0.5 s of PCM → no turn.
- Turn order: `turn_started` → planner (WAV + JPEG; `--planner yibu` runs
  the bounded tool loop — `inspect_objects` / `start_generation` /
  `emit_scene_ops`, max 4 rounds — then one tools-disabled closing call
  for the final line) → at most 3 schema-valid `scene_op`s (list frozen
  first) → wait 1500 ms for ACKs → one `speak` with cloud PCM in
  `speak.audio` (16 kHz mono s16le, Gemini Live leg; `audio: null` only
  when no synthesizer is attached or synthesis fails). Speech claims a
  drawing only if every op ACKed `placed`/`applied`; rejected/stale use
  the honesty copy; a missing ACK says "I couldn't confirm placement."
  once, and a late ACK never speaks again. Every turn records
  `voice_gate` (`passed` / `failed` / `degraded`) in session context.
- `cancel {turn_id}` tombstones the turn: no further ops or speech for it.
- Planner seam: `provider/coordinator/planner.py` (`Planner.plan` →
  `PlanResult`). Spatial prompt/parse is Member A's `spatial_ops.parse_model_reply`
  when present; a JSON-extract fallback stands in until then.
- Headset-free end-to-end: `python -m tools.fake_quest --say "mark the laptop"
  --jpeg photo.jpg` (`--reject`, `--no-ack` for honesty paths).
- Quest renders `mark`, `label` (billboard card), `ghost` (rotate/slide),
  `connect`, `place_procedural` (local composition: arrow/pointer/panel/
  cube/sphere/cylinder, closed palette/sizes/materials), and
  `revise_procedural` (enlarge/shrink/rotate/nudge/remove by drawing_id),
  plus cloud-PCM `speak` playback with caption (`QuestDemo/Assets/Voice/`,
  `Spatial/Procedural/`). Full GLB import is still open (placeholder cube).

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
- Voice proof runbook (needs headset + LAN + explicit credit go-ahead):
  1. Laptop: `cd provider && . .venv/bin/activate && python -m coordinator.server
     --planner yibu` (spends credit; key must be valid).
  2. Optional credit smoke first (no headset):
     `python -m omni.reasoner_smoke --purpose smoke_omni_tools --max-tokens 32`.
  3. Quest: set `laptop_ipv4`, hold talk trigger, say "mark the inlet valve",
     release. Expect: ghost/label/procedural pin on the surface, then cloud
     speech playback with caption.
  4. Say "generate that as a mesh" (or trigger a `start_generation` turn);
     when the job is ready expect auto `place_generated`, real GLB render
     (glTFast; cube means import fell back), and a short announce line.
  5. Check laptop logs: `voice_gate` is `passed` for the turn; any `failed`
     fails the voice-demo gate even if placement succeeded.
  6. Barge-in: press PTT during speech → `cancel` stops the turn, no late
     speech after the ACK.
