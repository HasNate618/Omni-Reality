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

**Tracking integration:** `--sam2-url ws://127.0.0.1:8766` with `--planner
stub|yibu` opts into a separate object-tracking turn. Quest's
`QuestStreamInput` sends real JPEGs and A-button push-to-talk PCM. The selected
frame and Huawei's points (up to 3) seed the existing SAM 2 click protocol;
results return as `tracking_result` instead of spatial `scene_op`s. Follow
[Quest camera + push-to-talk setup](quest-audio-setup.md); the frame contract
lives in [omni-sam2-streaming.md](omni-sam2-streaming.md#quest--voice-automatic-initialization).
The existing placement/ACK turn below remains the default without that flag.

- Coordinator runs voice turns only with a planner:
  `python -m coordinator.server --planner stub` (offline) or `--planner yibu`
  (live `qwen3.8-omni-flash`, spends credit). Default `--planner mark` keeps
  the slice-2 hardcoded mark above.
- Coordinator runs voice turns only with a planner. Default `--planner mark`
  keeps the slice-2 hardcoded mark above. Spatial voice (JPEG + tools + scene
  ops) uses `--planner yibu` without `--voice-only` (see
  `docs/omni-worker-tools.md`).
- **Headset bootstrap transport proof (offline, no credit):** from `provider/`,
  `python -m coordinator.server --planner voice-stub` — returns a fixed caption
  and a deterministic non-speech 16 kHz test tone (mic/WebSocket transport
  only, not Omni speech).
- **Headset bootstrap live audio-only smoke (spends credit):** from `provider/`,
  `python -m coordinator.server --planner yibu --voice-only` — PCM-only turn,
  no JPEG or spatial tools; cloud reply plays through Quest `SpeakCloudPlayer`.
  Requires explicit go-ahead and a non-empty `YIBU_API_KEY` on the laptop only.
  If the key is unset, the process exits immediately with a configuration error
  (names `YIBU_API_KEY` only; no WebSocket listen, no API call). A rare
  in-turn `SystemExit` from the planner is converted to redacted
  `planner_failed` + the safe fallback speak line without killing the session.
- Legacy offline planner: `--planner stub` (non-voice-stub slice-3 stub path).
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

## VoiceBootstrap diagnostics (redacted)

Controller-free mic → websocket → coordinator → cloud PCM playback emits
`VoiceBootstrap` lines with **counts and types only**. They never log API keys,
PCM/base64, transcripts, captions, prompts, model replies, URLs, or full
exception text.

- **Quest logcat:** `adb logcat -s Unity | grep VoiceBootstrap` (or
  `adb logcat | grep VoiceBootstrap` while exercising voice).
- **Laptop coordinator:** stderr/stdout from `python -m coordinator.server …`
  with `grep VoiceBootstrap`.

Quest events: `mic_permission`, `mic_started`, `mic_failed`, `vad_opened`,
`onset_dropped`, `vad_ended`, `websocket_connected`, `hello_accepted`,
`socket_failure`, `audio_drop_offline`, `utterance_end_drop_offline`,
`playback_accepted`, `playback_rejected`, `playback_started`,
`playback_finished`.

Coordinator events: `connection_open`, `connection_close`,
`utterance_end_accepted`, `turn_started`, `planner_complete`, `synth_result`,
`planner_failed`, `synth_failed`.
- Quest renders `mark`, `label` (billboard card), `ghost` (rotate/slide),
  `connect`, `place_procedural` (local composition: arrow/pointer/panel/
  cube/sphere/cylinder, closed palette/sizes/materials), and
  `revise_procedural` (enlarge/shrink/rotate/nudge/remove by drawing_id),
  plus cloud-PCM `speak` playback with caption (`QuestDemo/Assets/Voice/`,
  `Spatial/Procedural/`). Full GLB import is still open (placeholder cube).

Tracking uses status/result events rather than the placement-ACK speech path.
Its Unity source does not implement speech playback or a new passthrough renderer.

## Two modes: A push-to-talk, B conversation

One coordinator session serves both. The route is picked **per utterance** from
`payload.mode` on `utterance_end` (`"ptt"` or `"live"`), not from a startup
flag; a payload without `mode` falls back to `--perception-qa` if set, else
push-to-talk, so an older headset build still works.

| | **A — push to talk** | **B — conversation** |
| --- | --- | --- |
| Mic | `QuestStreamInput` (hold A) | `MicUtterance`, continuous VAD |
| Model | `qwen3.8-omni-flash` per turn | `gemini-3.1-flash-live-preview` Live session |
| Speech out | Android TTS (`QuestSpeech`), no credit | cloud PCM `speak_chunk`/`speak_final` |
| Overlay | seeds SAM 2 from the pinned frame | seeds on a tracking phrase (below) |

Both reach yibuapi with the same `YIBU_API_KEY`; only the model differs.

- **Mic ownership is exclusive, frame streaming is not.** B hands the
  microphone to `MicUtterance` (`CoordinatorClient.SetLiveConversation`, which
  toggles the component so `OnDisable` calls `Microphone.End`).
  `QuestStreamInput` keeps streaming JPEGs in both modes, so a seeded mask
  keeps tracking while you talk.
- **Buttons:** right **A** = push-to-talk, right **B** = conversation toggle,
  left **X** = stop tracking (moved off B).
- **Overlays mid-conversation:** the Live session returns speech, not
  coordinates, and the gateway **never sends `inputTranscription`** — the
  setup asks for it, but only `outputTranscription` arrives — so there is no
  transcript of the wearer to trigger on. Each conversation turn instead runs
  A-mode's `YibuPlanner(tracking=True)` in the background over the same audio
  and frame; it answers `track:null` unless an object was actually asked for,
  which is a better arbiter than phrase matching. `parse_tracking_reply` gives
  the point and `Sam2Bridge.seed` takes it unchanged. Fenced by
  `bridge.generation`, silent on failure rather than talking over a live reply.
  Costs a second model call per turn: `OMNI_LIVE_TRACK=0` disables it.
  `wants_tracking` remains for the day the gateway does send a transcript.
- **Prerequisite:** B-mode needs `ffmpeg` on the laptop (`brew install
  ffmpeg`) — model audio arrives at 24 kHz and is resampled to 16 kHz for
  Quest. Without it the conversation is silent and `test_resample` /
  `test_live_turn` / `test_perception` fail.
- Not yet: tool-calling in the Live session would let the model return the
  point itself and save the second call.

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
`TrackingResult.cs` expects. It creates itself at startup (no scene wiring) and
subscribes to `CoordinatorClient.TrackingResultReceived`.

Each tracked object gets its own layer, keyed by `obj_id`: its `mask_b64` is
tinted into its own RGBA texture and drawn on its own quad, built from the
capture frame's intrinsics and pose. Colours come from `Palette` in the same
order as `COLORS` in `sam2/sam2_ws_client.py`, so the webcam demo and the
headset give one object the same colour. Depth is measured per object from that
mask's own centroid, which is why the layers are separate: a laptop at 0.8 m and
a poster at 3 m cannot share one plane. The model's `label`, when it gave one,
floats over the mask.

Each quad is world-locked to the capture pose, so it holds still while the
wearer moves; it is a flat projection, exact along the capture ray and
approximate off-axis. A layer hides when its object stops coming back, and all
of them hide on `tracking_status` `stopped`/`error` or after 2 s with no result.
Without this component the masks arrive and are only logged
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
- Voice proof runbook (needs headset + LAN):
  1. Offline transport: `cd provider && . .venv/bin/activate && python -m
     coordinator.server --planner voice-stub` — expect test-tone caption on
     Quest after a ≥0.5 s utterance (no yibu calls).
  2. Live audio-only smoke (explicit credit go-ahead): same venv, confirm
     `YIBU_API_KEY` is set, then `python -m coordinator.server --planner yibu
     --voice-only`. Missing key → immediate config exit (no headset connection
     needed to reproduce).
  3. Full spatial voice (later slice-3 demo): `python -m coordinator.server
     --planner yibu` (spends credit; key must be valid).
  4. Optional credit smoke first (no headset):
     `python -m omni.reasoner_smoke --purpose smoke_omni_tools --max-tokens 32`.
  5. Quest: set `laptop_ipv4`, hold talk trigger, say "mark the inlet valve",
     release. Expect: ghost/label/procedural pin on the surface, then cloud
     speech playback with caption.
  6. Say "generate that as a mesh" (or trigger a `start_generation` turn);
     when the job is ready expect auto `place_generated`, real GLB render
     (glTFast; cube means import fell back), and a short announce line.
  7. Check laptop logs: `voice_gate` is `passed` for the turn; any `failed`
     fails the voice-demo gate even if placement succeeded.
  8. Barge-in: press PTT during speech → `cancel` stops the turn, no late
     speech after the ACK.
