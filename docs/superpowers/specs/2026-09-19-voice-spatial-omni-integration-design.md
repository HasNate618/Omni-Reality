# Voice spatial omni integration: binding design

Date: 2026-09-19.

Status: binding for the next hackathon milestone. It narrows
`docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`
(parent spec) to a voice-driven demo with bounded procedural generation.
Where this spec is silent, the parent spec, `docs/omni-provider-api.md`,
and `docs/omni-spatial-loop.md` still bind. Where they conflict on the
voice turn, cloud speech, or procedural ops, this spec wins.

Hardware and live provider calls are unrun in this branch. The acceptance
gates in §9 name what must still be proven on device and with credit.

## 1. Product and hero loop

A voice-driven spatial assistant on Quest 3S that demonstrably (a)
understands the viewed environment from speech plus a fresh image,
(b) interacts with its own virtual objects by revising them, and
(c) generates bounded procedural 3D content from speech.

The hero loop is one bounded voice turn:

1. Local keyword activation ("Hey Omni") opens a single VAD-bounded turn.
   Push-to-talk (controller trigger labeled talk) is a required activation
   fallback and behaves identically downstream. There is no true
   full-duplex or barge-in in this milestone: opening a new utterance
   while a turn is active cancels the old turn under the parent-spec
   tombstone rules; mid-playback interruption stops local playback and
   starts a new turn, it does not duplex audio.
2. Quest sends one fresh capture JPEG with its capture envelope plus
   bounded 16 kHz mono PCM for that utterance over the existing LAN
   WebSocket.
3. The laptop coordinator runs cloud Omni reasoning over that audio and
   image, with tools enabled, producing at most 3 validated
   world-grounded operations.
4. Quest renders or rejects each op and ACKs every one.
5. Only after all terminal ACKs arrive does the coordinator ask the model
   for its truthful final line (tools disabled), synthesize that line as
   cloud speech, and send playable PCM to Quest.
6. Quest plays the cloud PCM and shows the matching caption and status.

A turn with no ops still follows the same barrier: reasoner output with
tools enabled, `ops_closed` with an empty list, final line with tools
disabled, then cloud speech.

## 2. Model and audio routes

Cloud Omni does every turn's audio-plus-image understanding and spatial
planning. The baseline reasoner is the documented HTTP Omni reasoning
route (`POST https://yibuapi.com/v1/chat/completions`, OpenAI-compatible,
built with the `provider/` HTTP helpers): raw mic PCM of at least 0.5 s
plus the turn JPEG plus transcript text go into the call, using the
documented audio and image content shapes. Transcript-only reasoner calls
do not pass. The reasoning model baseline is `qwen3.8-omni-flash`
(multimodal in, text out), consistent with the parent spec and the
provider registry.

The reasoner output is typed protocol data only: `emit_scene_ops`
arguments plus text. The model never emits Unity code, C#, URLs, file
paths, mesh references, or world coordinates. The coordinator validates
every op against the protocol schemas before it becomes a `scene_op`;
anything unparseable, off-grammar, or over the per-turn op cap is dropped
and treated as a zero-op or partial-op result, never forwarded raw.

Cloud-generated speech is required for the voice demo, not an
on-device-TTS success path. After the ACK barrier and the final model
line, the coordinator synthesizes speech through a separate documented
cloud audio route and returns raw PCM in `speak.audio` (16 kHz mono
s16le, same shape as the parent spec). The primary candidate is the
documented Gemini Live WebSocket route, which returns audio bytes. The
Qwen realtime WebSocket route is an optional gated alternative: it may
replace the Gemini speech leg only after a smoke proves audio out plus
whatever modalities the chosen split needs. On-device TTS and captions
remain as degraded presentation only. If cloud speech fails, Quest shows
an honest caption and error chip, and the turn is recorded as failed at
the voice-demo acceptance gate even if placement succeeded.

Every live provider call carries a short `--purpose` label and is
audit-logged to the gitignored ledger exactly as `docs/omni-provider-api.md`
requires. The key lives only in `YIBU_API_KEY` on the laptop. It never
enters Unity, the repo, logs, or any message to Quest. Audit records
carry token counts, latency, endpoint, and redacted errors only: no
request media, no prompt or transcript text, no response body, no key
material. Missing token counts stay `null`. Live calls stop before the
documented key expiry; usage summaries follow the documented reporting
rules.

## 3. Spatial operations

All parent-spec rules stay in force: `mark`, `label`, `connect`, and
`ghost` semantics, motion kinds and defaults, acknowledgement statuses
and reasons, honesty chips, capture-time pose discipline, the 10 s
per-frame geometry cache, the same-ray 0.12 m stale rule, hit range
0.25–4 m, the 8-drawing cap, last-writer-wins per `drawing_id`, and the
`clear_session` generation fence. This spec adds exactly one op family
(`place_procedural`) and one revision family (`revise_procedural`).

### 3.1 `place_procedural`: bounded local generation

`place_procedural` is a planned, schema-validated `ModelSceneOp` kind for
generation. It composes Quest-local procedural geometry only. There is no
mesh download, no mesh import, no URL, no file path, no code field, no
model-supplied world point, and no physics or real-world manipulation.

The model supplies only semantic content within a closed grammar:

- Element kinds: `arrow`, `pointer`, `panel`, `cube`, `sphere`, `cylinder`.
  `arrow` and `pointer` are directional helpers; `panel` is a small
  billboarded text card; the rest are solid primitives.
- Composition cap: at most 6 elements per op, at most one op of this kind
  per turn unless the second op is a `remove` of the first.
- Text: only `panel` and `pointer` carry text, at most one string each,
  at most 40 characters, plain text only.
- Colors: closed palette of six (`cyan #3DDCFF`, `amber #FFB020`,
  `green #35D07F`, `magenta #E05CFF`, `white #FFFFFF`, `slate #8A93A6`).
  One color per element.
- Size semantics: each element names a size class, `small`, `medium`, or
  `large`, never metres. Quest maps classes to fixed extents within
  0.03–0.40 m per element, and the whole composition must fit inside a
  1.0 m bounding sphere or Quest rejects with `invalid`.
- Material: closed preset per element, `solid`, `translucent`, or `glow`.
  No shader, texture, or lighting parameters.
- Placement target: the same target types as other ops (`capture_hint`,
  `image_point`, `image_box`, `pointing`); never a world point. Quest
  resolves the anchor with the same capture-time raycast, range, and
  stale rules as a `mark`, then builds the composition in the hit tangent
  frame at headset framerate.

Validation failures use the existing ACK vocabulary: `invalid` for
grammar violations, `clutter` for the drawing cap, `rejected` with the
standard reasons for bad anchors, `stale` when capture geometry expired
under the same-ray rule. A rejected or stale `place_procedural` blocks
success speech exactly like any other op.

### 3.2 `revise_procedural`: bounded revision of Omni-created drawings

`revise_procedural` is a planned, schema-validated `ModelSceneOp` kind. The
model may revise only drawings that Quest created from a prior
`place_procedural` op in the same app run. It may not revise `mark`,
`label`, `connect`, `ghost`, or any drawing it did not create through
this family. Revisions name the target `drawing_id` and exactly one
semantic action:

- `enlarge` / `shrink` (fixed Quest-side scale steps of ×1.25 / ×0.8,
  clamped to the §3.1 per-element and bounding-sphere limits),
- `rotate_cw` / `rotate_ccw` (fixed 15-degree Quest-side yaw step in the
  anchor tangent frame),
- `nudge` with a closed direction vocabulary (`left`, `right`, `up`,
  `down`, `forward`, `back`, fixed 0.05 m Quest-side step along the
  tangent frame or surface normal as appropriate, re-pinned to the
  surface or rejected with the standard reasons),
- `remove` (deletes the drawing, ACKs `applied`).

The model sends directions and action names only. It never sends metres,
degrees beyond the named action, quaternions, positions, or scales.
Quest converts each semantic action to concrete transform values and
rejects anything that would push the composition off the surface, out of
range, over the size cap, or past the drawing cap. Revision follows the
same ACK barrier as placement: the final spoken line must reflect the
terminal ACK, and a failed revision is reported honestly.

## 4. Turn state machine and placement authority

Placement authority stays on Quest. The coordinator never forwards raw
model output as a `scene_op`; every op is schema-validated,
cap-checked (at most 3 per turn), stamped with coordinator-assigned
`op_id` / `turn_id` / current `stage_epoch`, and sent only after
`turn_started`. Quest validates, raycasts at capture time, renders, and
ACKs each `op_id` with `placed` / `applied` / `rejected` / `stale`.

The ordered turn lifecycle:

1. `activation`: keyword or push-to-talk opens `utterance_id` on Quest.
2. `capture_send`: Quest streams bounded PCM chunks plus one fresh frame
   (envelope plus JPEG) tagged with that `utterance_id`, then
   `utterance_end`.
3. `turn_started`: coordinator assigns monotonic `turn_id`, sends
   `turn_started`, and opens audit for the turn.
4. `reason_tool`: coordinator calls the HTTP reasoner with audio, image,
   and text, tools enabled; validates and freezes at most 3 ops;
   records `ops_closed`.
5. `ack_barrier`: coordinator sends the frozen `scene_op`s, waits for a
   terminal ACK on each (parent-spec 1500 ms budget). Any `rejected` or
   `stale`, any missing ACK at timeout, or any `stage_epoch` supersede
   ends the placement phase without success speech.
6. `final_line`: coordinator returns tool results to the model and
   requests the spoken follow-up with further tools disabled. The model
   must state only what the ACKs confirm.
7. `cloud_speech`: coordinator synthesizes the final line through the
   cloud audio route and sends `speak` with text plus playable PCM.
8. `playback`: Quest plays the PCM, shows the caption, and reports
   playback status. The turn closes on playback end or playback error.

Terminal failures: activation with no audio (drop, no turn); capture or
socket failure before `turn_started` (drop, Quest shows offline if the
socket is down); reasoner or tool-output failure (single honest error
line, no ops, audit-logged); ACK timeout, reject, or stale (no success
speech; coordinator may `request_frame` and wait for a new utterance,
never auto-retry the same `op_id`); final-line failure (no `speak`);
cloud-audio failure (honest caption and error chip; voice-demo gate
fails); playback failure (turn failed, drawings already placed stay
under last-writer-wins). Cancel from a newer utterance tombstones the
older `turn_id` under parent-spec rules; late packets for tombstoned
turns change nothing and trigger no speech.

## 5. Responsibilities

Quest owns activation and fallback (on-device keyword spotter,
push-to-talk, single-turn VAD window), local capture (fresh JPEG,
envelope, 16 kHz PCM chunking at roughly 100 ms), capture-time geometry
(poses, raycasts, the 10 s cache, tangent frames), transport priority
(`cancel` and `ack` ahead of frame bytes), render and ACK (validation,
anchors, procedural factory output, motion loop at headset framerate),
and playback (cloud PCM playback, caption, honesty chips, offline
behavior that keeps existing drawings and swallows new utterances).

The laptop owns the turn lifecycle (utterance assembly, `turn_started`,
`ops_closed`, ACK barrier, tombstones, generation fence), audit (purpose
labels, ledger, redaction, missing-count discipline), model adapters
(HTTP reasoner adapter; cloud speech adapter), validation (schema checks,
op cap, `stage_epoch` and `turn_id` stamping), the tool-to-speech barrier
(no `speak` before terminal ACKs plus a tools-disabled final line), and
secret hygiene (key read from the environment only, never forwarded).
The coordinator never imports procedural mesh code and never sends
coordinates; Quest never calls the provider and never sees the key.

## 6. Privacy

There is no continuous raw audio or image recording. Quest retains only
the bounded buffers for the open utterance and the fresh frame for that
turn; the laptop retains only transient per-turn audio, frame, and
transcript buffers plus the gitignored audit ledger. All transient media
clears on `clear_session`, on turn error or cancel, and on socket drop.
Session media stays under gitignored laptop paths; no JPEG, WAV, PCM, or
transcript is committed. The keyword implementation runs fully on device
and never sends pre-activation audio off device; only the post-activation
bounded turn leaves the headset, addressed to the laptop over LAN, never
directly to the provider.

## 7. Team split and merge order

Work splits into four narrow lanes with one writer per path at a time;
the collaboration contract in `docs/hackathon-collaboration.md` governs
branching, handoffs, and merge order, and is not restated here.

- Integration lead (this spec's owner): coordinator turn orchestration
  for the voice lifecycle (§4), the schema and transport seam
  (`place_procedural` and revision shapes, `speak.audio` PCM path), and
  the narrow `CoordinatorClient` boundary used by voice and procedural
  playback. Reviews and merges all lanes.
- Teammate 1, cloud brain plus audio: new `provider/omni/` package only
  (HTTP reasoner adapter with tool validation, cloud speech adapter,
  fakes for both, credit-spending smokes). No coordinator logic edits,
  no Unity edits, no schema edits except through the integration lead.
- Teammate 2, Quest voice UX: new `QuestDemo/Assets/Voice/` components
  and events only (keyword, push-to-talk fallback, VAD window, PCM
  chunking, playback, caption and chip wiring). No coordinator or schema
  edits; consumes the `CoordinatorClient` boundary as given.
- Teammate 3, procedural spatial factory: new
  `QuestDemo/Assets/Spatial/Procedural/` factory only (grammar
  realization, size-class and material tables, semantic revision steps,
  anchor-frame composition) plus the demo scene and hardware acceptance
  runs. No dispatcher or transport edits.

Merge order is baseline first, then lanes smallest-first through the
integration owner with the verification gate rerun after each merge, per
the collaboration doc. Unreviewed or unpushed work waits. Teammate 1
fakes land before Quest playback needs real PCM; Teammate 3's factory
lands behind its new directory so Quest voice work is never blocked.

## 8. Verification

Offline unit and contract tests (no credit, no headset) extend the
existing provider suite: schemas and fixtures for `place_procedural`
and revision actions (valid plus each grammar violation), op-cap and
unknown-kind rejection, semantic-action tables (no metre or quaternion
inputs accepted), ACK-barrier ordering (no `speak` before terminal ACKs
and a tools-disabled final line), send-priority preservation with PCM
frames, and audit redaction (no media, prompt, reply, or key material).

Credit-spending smoke gates (tiny prompts, short purpose labels) prove
each live leg once: the reasoner smoke sends raw PCM plus JPEG with a
tool call and returns validated op-shaped output; the cloud-audio smoke
returns PCM bytes that decode at 16 kHz mono and play on Quest. The
Qwen realtime alternative needs its own prior smoke before it may carry
any milestone traffic.

Hardware end-to-end (on device, on LAN) passes only if three scenarios
all pass: understanding (one spoken request about a visible object
produces a grounded mark or label with no success claimed before ACK,
and the ledger shows audio bytes on the reasoner call); interaction (a
follow-up such as "make it bigger" or "spin it the other way" revises
the same `drawing_id` and the old transform does not return); generation
(a spoken request such as "put a small arrow here pointing
left" yields a Quest-built procedural composition inside the size and
palette bounds, then a cloud-spoken confirmation plays audibly). Any
placement rejection, stale result, ACK timeout, or cloud-audio failure
fails that scenario's success claim. Local on-device speech counts only
as degraded presentation, never as the voice pass.

Out of scope for this milestone: arbitrary AI mesh generation or import,
bundled-GLB or generated-mesh placement jobs, object tracking, always-on
labeling, true full-duplex or barge-in audio, local GPU speech as the
primary voice, model outputs controlling C#, URLs, files, or world
coordinates, and any safety-critical claim the ACKs do not support. The
two local 4060 GPUs remain optional later workers for grounding or
generation experiments; they are not on the live voice path and this
scope allocates them nothing.

## 9. Acceptance and residual work

Accepting this milestone means the offline suite passes, both smokes
passed with ledger evidence, and the three hardware scenarios passed on
device with drawings stable while the wearer walks and honest chips on
every forced miss. This spec branch proves none of the hardware or live
gates: no headset runs, no live provider calls, and no audio played on
Quest have occurred here. The residual decisions are the exact demo
table object, the Horizon OS version on the borrowed headset (logged in
`hello` capabilities), and whether the Qwen realtime speech alternative
is worth its smoke cost given that Gemini audio is the primary path.
