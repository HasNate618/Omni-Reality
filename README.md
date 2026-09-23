# Omni Reality

A Quest 3S spatial assistant. It sees what you see, hears you talk, talks back,
and puts its answer in the room as world-locked geometry: marks on a real
surface, a path traced across a cable, a ghost that shows how a part moves, an
object standing at the size its listing states.

The model chooses what to show. The headset decides where it can go. The model
never sends coordinates or code, only scene ops that are validated against JSON
Schema before anything renders.

The Hack the North demo video shows an earlier build of the conversation loop.

## What it does

1. You ask about what you are looking at. The turn carries a camera frame,
   microphone audio, and the transcript to the omni model.
2. You ask it to show you something. "Show me the red cable" seeds a SAM 2 mask
   that tracks the object in passthrough for ten seconds, then fades.
3. You ask for drawings. Marks, labels, paths, and ghost motion land on real
   surfaces, anchored to the pose from the moment the frame was captured, not
   the pose your head is in when the reply arrives.
4. You ask for an object at a size. The listing's stated dimensions become a
   life-size box first, and a generated mesh fills that box without stretching.
5. You interrupt and correct. Barge-in tombstones the turn, and the replacement
   drawing reuses the same drawing id.

## Conversation

Turns are multimodal: a JPEG, at least 0.5 s of 16 kHz mono PCM, and text. The
coordinator runs a bounded tool loop of at most four rounds, then makes one
tools-disabled closing call for the spoken line.

Model tools are `emit_scene_ops` (at most three ops per call), `inspect_objects`,
`start_generation`, `place_item`, and `highlight_object`.

Interruption is a first-class path. A cancel tombstones the turn, stops speech,
and drops late ops so they never render. Working context keeps recent turns,
active targets, live drawings, and recorded listings, which is what lets "that
one" and "the other one" resolve.

## Tracking highlight

`highlight_object(label, u, v)` arrives from the Live session with coordinates
in the question photo the model saw. The bridge seeds SAM 2 on that frame only;
a tombstoned turn never seeds, and a new turn stops a live track first.

The headset streams about 5 fps of small frames only while a track is live, and
stops when the status leaves selecting, initializing, or tracking. The overlay
holds for ten seconds and fades over 0.75 s. The TTL is server authoritative,
so the overlay clock can hide a track early on loss but never extend one. A
stage change resets the bridge. A failed seed fails honestly and says so.

## Spatial drawing

Every image that leaves the headset carries a capture envelope: camera side,
intrinsics, crop, distortion, capture-time pose, stage epoch, and frame id.
Placement uses the capture-time ray, and per-frame geometry is cached for ten
seconds.

Scene ops are `mark`, `label`, `connect`, `ghost`, `place_procedural`,
`revise_procedural`, `place_generated`, `remove`, and `undo`. Motion kinds are
`pulse`, `travel`, `rotate`, and `slide`, animated in the surface tangent frame
and looping at headset framerate with no further model calls.

The headset acknowledges every op, and the coordinator speaks only after those
acknowledgements land, so the model never claims a drawing arrived before it
did. Rejections map to honesty chips: no surface, stale, too close, out of view,
offline. Stale placements are rejected rather than guessed, and the scene holds
at most eight drawings.

## Generated objects at stated size

`place_item(name, extent_m, target)` requires the listing's own width, depth,
and height in metres. The model may not place a listing it cannot size, and it
asks instead.

The coordinator validates the metres, records the listing row, authors the
`place_generated` op with `extent_m`, and plants a box at accept time, so the
wearer sees the stated size while the mesh is still baking. Workers run on
loopback HTTP: SAM 2 for inspect, and a mesh generator for artifacts. One
generation job runs per session, and a name in the pre-baked registry is placed
without queueing a worker at all.

When the mesh arrives, it fits the box with a single uniform scale factor.
Stretching to fill is rejected, because a stretched mesh would make a
misproportioned generation look correct while lying about the object. If the
mesh aspect differs from the box by more than 25 percent, the box stays visible
and the wearer is told the mesh is approximate. Artifacts are fetched by ULID
job id from the laptop's artifact port.

## Voice

The headset captures speech behind push-to-talk or a VAD gate, frames it per
utterance, and runs an echo gate so playback does not feed back into the mic.
Speech out uses cloud PCM from the Gemini Live leg at 16 kHz mono s16le when a
synthesizer is attached, and falls back to captions when it is not. Each turn
records its voice gate result as passed, failed, or degraded.

## Protocol, coordinator, and privacy

The headset and laptop speak one WebSocket of JSON messages with ULID ids and
stage epochs. The send queue is priority ordered, so a cancel never waits behind
a frame. Ops are validated against JSON Schema fixtures before rendering.

Every gateway call appends a redacted row to a JSONL ledger with the purpose
label, model, transport, latency, and token counts. Prompts, media, replies, and
keys never enter the ledger, and a missing token count stays `null` rather than
becoming zero. `YIBU_API_KEY` lives in the laptop environment only; Unity never
calls the cloud.

## Architecture

```
Quest 3S (Unity C#)              Laptop (Python)                 Cloud
─────────────────────            ────────────────────            ─────────────────
camera, mic, pointing            coordinator                     omni model
depth, raycast, anchors           session, turns, tools          vision + audio in,
render, occlude, animate          tombstones, audit ledger        text + tool calls out
placement ACK  ◄──────────────── WebSocket JSON                  TTS voice out
                                  protocol schemas
                                  workers on loopback HTTP
                                   SAM 2 inspect, mesh generation
```

## Repo layout

| Path | Contents |
| --- | --- |
| `QuestDemo/` | Unity 6 LTS app: capture envelope, placement, scene-op rendering, ghost motion, grab rig, honesty chips, tracking overlay, voice capture and playback |
| `provider/coordinator/` | Session, turns, planner, job store, listings, layout packing, pre-baked registry, SAM 2 bridge |
| `provider/omni/` | Tool definitions, reasoner with the bounded tool loop, smoke entry point |
| `provider/protocol/` | Schemas, fixtures, id generation, stale rules, UV convention, validation |
| `provider/voice/` | Audio framing, echo gate, resampling, cloud speech, live session |
| `provider/workers/` | SAM 2 and mesh generation servers and clients |
| `sam2ws/` | SAM 2 streaming server, segmentation, cutout, harness and viewer clients |
| `docs/` | One doc per topic, listed below |
| `docs/superpowers/` | Per-slice design specs and TDD implementation plans |

## Running

Offline tests need no headset and no API key:

```bash
cd provider && . .venv/bin/activate
python -m unittest discover -s tests -v
```

Live smoke spends credit, so keep it tiny:

```bash
cd provider && . .venv/bin/activate
python -m omni.reasoner_smoke --purpose smoke_omni_tools --max-tokens 32
```

Workers run from `provider/workers/run_workers.sh`. The coordinator serves the
LAN WebSocket, and `provider/tools/fake_quest.py` stands in for the headset
during laptop-only work.

Building and deploying the headset app uses the flake shell and the licensing
wrapper described in `docs/questdemo-build.md`.

## Documentation

- `docs/omni-spatial-loop.md`: capture envelope, placement, scene ops over LAN
- `docs/omni-tracking-highlight.md`: highlight tool, SAM 2 bridge, overlay TTL
- `docs/omni-worker-tools.md`: model tools, generation jobs, artifact rules
- `docs/omni-sam2-streaming.md`: SAM 2 server and client protocol
- `docs/omni-provider-api.md`: gateway contract, models, audit rules
- `docs/questdemo-build.md`: Unity to Quest build and verification
- `docs/omni-live-pitch.md` and `docs/omni-live-research.md`: product and research notes
- `demo.md`: the locked demo run-of-show

## Next

- Corner measurement: two-ray wall measure, tolerance chip, numeric fit verdict.
- Listing photos as generation seeds instead of worker defaults.
- Hand tracking for grab alongside controllers.
- Listing memory across sessions.

Hack the North 2026, Huawei OMNI Live track.
