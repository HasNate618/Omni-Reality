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
`PassthroughCameraAccess.GetTexture()` (left camera, already displayed
live on the CameraQuad by `ARRuntime`). At VAD close, Quest reads back
that texture once, downscales (longest side <= 640 px, starting point ~612x408
from the proven smoke), JPEG-encodes to <= 64 KB, and attaches it to the
closing utterance. Exact dimensions/quality are pinned in the implementation
plan; the smoke's 34 KB frame is the reference point. If the camera is not
playing, encoding exceeds the cap, audio is under 0.5 s, or the socket is
down, Quest drops the frame and runs a voice-only fallback turn.

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
- **`CoordinatorClient` + `ProtocolJson` (small edit):** `BuildFrame`
  gains a `jpeg_b64` form for voice turns only (today it sends envelope
  only). Priority stays below cancel/ack.
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
4. Coordinator assembles; under-length/missing/bad frame becomes a
   voice-only fallback (existing `voice-only-turn` path, no image).
   Otherwise Omni audio+JPEG (`perception-qa-turn`) → short text → TTS
   (`perception-qa-speak`) → `speak`.
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
recorded while silent. Exactly one Omni call plus one TTS call per
accepted turn, small `--max-tokens`, labeled purposes, redacted ledger,
missing counts stay null. Live coordinator remains stopped unless a
bounded test is explicitly requested.

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

Point selection on the captured image → capture-time raycast → world pin;
SAM2 boxes/masks shown to the model and the wearer in real time; mask
crops feeding image-to-3D. Those reuse the `utterance_id` / `frame_id`
linkage above and live in the worker-tools / voice-spatial lanes.
