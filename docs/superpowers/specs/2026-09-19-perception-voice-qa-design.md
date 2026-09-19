# Perception voice-Q&A: binding design

Date: 2026-09-19.

Status: binding for headset "ask about my surroundings" over the real
camera path. Narrower than `2026-09-19-voice-spatial-omni-integration-design.md`:
this milestone answers what the wearer sees and hears. It creates no
drawings, runs no tools, and streams no video.

## 1. Goal

A wearer speaks hands-free, and the laptop answers with cloud Omni
reasoning over that speech plus exactly one fresh camera frame, then
speaks the answer back through the headset. Success is a correct spoken
answer about something visible — not a mark, label, or generated object.

## 2. Explicit exclusions

No `scene_op` of any kind. No `mark`, `label`, `ghost`, `connect`,
`place_procedural`, `revise_procedural`, or generated-mesh work. No SAM2
masks, no tracking, no always-on labeling, no continuous video, no
barge-in. The existing controller trigger stays a developer fallback and
is not required. Point-based grounding, SAM2 highlight/mask display, and
mask-to-image-to-3D extraction are reserved future consumers — this spec
does not build them.

## 3. Architecture (as built, not as wished)

Quest owns VAD, capture, transport, playback. Laptop owns turn IDs, Omni
call, audit, TTS. One WebSocket, no new services or ports.

The camera source is the real one already on device:
`PassthroughCameraAccess` left texture (already displayed live on the
CameraQuad by `ARRuntime`). At VAD close, Quest requests one frame through
`AsyncGPUReadback` in render order (a blocking Blit/ReadPixels can return
the previous image per the SDK warning), freezes capture-time pose +
intrinsics + timestamp first, downscales with aspect preserved (longest
side <= 640 px; full 1280x960 input becomes 640x480), JPEG-encodes at
quality 60 with one quality-35 retry to stay <= 64 KB, and attaches it to
the closing utterance. The request waits at most 750 ms and requires a PCA
update no older than 500 ms; AsyncGPUReadback must be supported. Audio is
never re-sent: PCM already streamed stays streamed, and the JPEG is queued
in the same FIFO media lane before the single `utterance_end`. If the
camera is not playing, capture/readback/encode fails, audio is under 0.5 s,
or the socket is down, Quest drops the media locally with a visible
recovery hint and makes no cloud call. A missing or rejected image on an
otherwise valid turn yields a fixed local recovery reply (no Omni call), not
an audio-only model answer. No offline replay or cloud fallback ever occurs
on disconnect.

The coordinator runs the existing grounded HTTP Omni helper
(`build_voice_messages` audio WAV + JPEG) in a new `perception-qa` planner
mode with tools disabled and ops forced empty, then the existing Gemini TTS
leg. Audit purposes are `perception-qa-turn` (Omni) and `perception-qa-speak`
(TTS) so this credit is separable from voice-only and grounded turns.
`voice-only-turn` keeps meaning audio-only with no image.

## 4. Components

- **Quest perception capture (new, next to Voice):** single-frame grab
  from the PCA texture at `utterance_end`; downscale, JPEG encode, byte
  cap; tag with the closing `utterance_id`. Drops on camera-down,
  over-cap, too-short, or offline. Never buffers across turns.
- **`CoordinatorClient` + `ProtocolJson`:** `BuildFrame` gains a
  `jpeg_b64` form for voice turns only (today it sends envelope only).
  Audio chunks, the single frame, and `utterance_end` share one FIFO media
  lane (cancel/ack stay ahead; `utterance_end` can no longer overtake audio
  or the image). Capture is negotiated: `hello_ok` carries
  `perception_qa`; Quest only sends images when the server offers it, and
  the server ignores images when the planner is not perception mode. Quest
  shows spoken and recovery text in a head-relative caption; logs never
  carry captions.
- **Coordinator planner `perception-qa` mode (new, beside `voice-only`):**
  audio + JPEG in, short plain-text answer out ("answer what you see and
  hear, no JSON, no ops"; one or two sentences, planner max-tokens <= 128),
  tools off, ops forced empty. Unknown or unclear content answers honestly
  instead of guessing.
- **No new scenes, shaders, permissions, or key paths.** Camera and mic
  grants already exist. `YIBU_API_KEY` stays laptop-env-only.

## 5. Data flow

1. VAD opens on speech; PCM streams in ~100 ms chunks as today.
2. 800 ms silence or 8 s max closes the utterance.
3. Quest sends `frame {utterance_id, envelope, jpeg_b64}` once, then
   buffered audio chunks, then `utterance_end` — same order as the
   grounded turn.
4. Coordinator assembles; under-length turns and any media after
   disconnect/close are dropped locally with no cloud call. A missing or
   rejected image yields a fixed local recovery reply with no Omni call.
   Otherwise one Omni audio+JPEG call (`perception-qa-turn`, max-tokens
   <= 128, no tools) → short text → one TTS call (`perception-qa-speak`)
   → `speak`. One question never overlaps the next: the mic gate holds
   until the reply or a 45 s timeout with visible feedback.
5. Quest plays PCM and shows the caption. All buffers clear on close,
   error, cancel, or socket drop. No replay.

`frame_id` / `utterance_id` linkage is preserved exactly so later SAM2
boxes/masks and point-targeted ops can attach without a transport change:
the model will pick image points, Quest will translate through the
capture-time raycast under the same-ray stale rule (0.25–4 m), and masks
can feed object-image extraction then.

## 6. Error handling and recovery feedback

Every fallback names its reason in redacted `VoiceBootstrap`
diagnostics — counts, byte sizes, and exception classes only. No PCM,
JPEG/base64, transcripts, prompts, replies, URLs, or keys.

- `frame_dropped {reason: camera_down | encode_over_cap |
  socket_down | too_short}`
- `jpeg_rejected`, `planner_failed`, `synth_failed`,
  `voice_gate=passed|failed|degraded`
- Quest logcat and coordinator stderr share the same `VoiceBootstrap`
  grep, so a failure points at camera, transport, Omni, or TTS without
  replaying media.
- Safety-critical guesses (live power, load ratings, food doneness)
  stay refused. Unclear targets get "I can't tell — look closer," never
  coordinates or a fake description.

## 7. Privacy and credit

Only the bounded open utterance plus its one frame leave the headset,
over LAN to the laptop — never directly to the provider. Nothing is
recorded while silent, and the 300 ms pre-roll is a local VAD buffer, not
cloud recording. At most one Omni call plus one TTS call per accepted
valid turn (invalid/short/offline turns make none), max-tokens <= 128,
labeled purposes, redacted ledger, missing counts stay null. Live
coordinator remains stopped unless a bounded test is explicitly requested.

## 8. Verification

- Offline (no credit): Quest EditMode capture-gate tests (drops when
  camera down / over cap / offline; one JPEG per utterance; no
  cross-turn buffering); provider tests for JPEG accept/reject, ops
  forced empty, diagnostics carrying no payload strings, audit carrying
  no media/text/keys.
- Credit smoke (tiny, labeled): one audio+JPEG Omni call proves verbatim
  hearing plus correct image description; one TTS call proves 16 kHz PCM
  at normal speed.
- Hardware pass: "what's the [object] on the left" → spoken answer
  matches the visible scene with caption and audible playback. Covered
  lens / dark frame → honest "couldn't see" plus the diagnostic reason.

## 9. Forward path (not built here)

Point selection on the captured image → capture-time raycast → depth/scene
hit → world pin; SAM2 boxes/masks shown to the model and the wearer in real
time; mask crops feeding image-to-3D. SAM2 masks alone do not supply metric
depth or current-view registration: real-time display needs tracking and
reprojection work. The `utterance_id` / `frame_id` linkage plus the
corrected sent→original pixel-centre mapping (`p_original =
(p_sent + .5) * scale - .5`) and the cached capture geometry are preserved
so that future work can add calibrated UV → capture ray → hit resolution;
they are provenance, not a promise that no future protocol fields are
needed. That work lives in the worker-tools / voice-spatial lanes.
