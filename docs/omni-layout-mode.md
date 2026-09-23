# Layout mode

Stand in an empty corner, summon a three-item cart as true-size translucent
boxes, and push them around with the controller until the corner works.

Layout mode answers one question — *will this furniture fit here* — and is
built so that it keeps answering it when the model is unreachable. The box is
the promise; everything else is decoration.

## What it is

| Piece | Where | Needs the key? |
|---|---|---|
| Canned cart (3 items, mm) | `provider/coordinator/cart.json` | no |
| Layout planner | `provider/coordinator/layout.py` | no (model optional) |
| `place_box` scene op | `provider/protocol/schemas/scene_op.json` | no |
| Furniture catalog and meshes | `Furniture{Catalog,Mesh,Selector}.cs` | no |
| Corner, drag, resize, fit, labels | `QuestDemo/Assets/Spatial/Layout*.cs` | no |
| Speech | `say_to_pcm` on the laptop (macOS `say`) | no |

**Nothing on this path calls the model.** That is a measurement, not a
preference -- see Latency below. Layout mode behaves identically before and
after the key expires, which is the whole point of it.

## Contract

`place_box` is coordinator-authored and is **not** in `model_scene_op.json`.
The model never emits geometry — the same rule that governs `place_known` and
`place_generated`.

```json
{
  "kind": "place_box",
  "target": { "type": "layout_slot", "dx": 1.05, "dz": 0.5, "yaw_deg": 0 },
  "size_m": { "w": 1.8, "d": 0.9, "h": 0.83 },
  "style": { "color": "#3DDCFF", "label": "2-seat sofa" }
}
```

**Slots are corner-relative, not world points.** The headset owns corner
detection, so the coordinator never needs room geometry and the whole path runs
offline. `dx` and `dz` are the footprint centre in metres from the corner,
running into the room; `yaw_deg` rotates the piece about the floor normal.

One corner frame is resolved per turn and shared by every box in it. Boxes from
a later turn clear the earlier set — otherwise three pieces end up in three
slightly different rooms.

`size_m` is the seller's stated size converted from millimetres. A box is
dropped rather than guessed at when any axis is missing, non-numeric, zero, or
over 4 m.

## Honesty

The research brief warns that fit guidance "needs known dimensions, reliable
calibration, or an appropriate CAD model." This has approximate numbers on a
depth-sensed floor, which is none of those. So:

- A caption reads **"Approximate sizes, not measured."** whenever boxes are
  placed. It is a `LayoutLabel` at more than
  three times `VoiceCaption`'s character size, because it has to be readable
  while walking around the corner, and it rides near the top of the view --
  tips sitting in front of the furniture obscure the thing they describe.
- Each piece carries its own name plate with the size in centimetres, so the
  claim is visible rather than implied.
- Every clearance line starts with "Roughly" and never states a bare figure.
- Overlap is reported as overlap, not as a negative clearance.
- Clearance is computed on yaw-expanded axis-aligned extents, which
  over-estimates a rotated footprint — erring towards "tight", never towards
  "it fits".
- The spoken line after placement says the sizes are approximate.

This is enforced by test, not by convention:
`LayoutTests.FitVerdictIsAlwaysHedgedAndNeverClaimsMeasurement`.

## Latency, measured 2026-09-20

| Step | Time |
| --- | ---: |
| `utterance_end` -> three `place_box` ops on the wire | **28 ms** |
| -> spoken line (`say_to_pcm`) | **772 ms** |

Identical with and without a key.

Two things were tried and rejected on evidence:

- **Model-chosen arrangement.** The model does produce a sensible layout, but
  it took **16 s** on one call and **39 s** on the next -- it reasons at length
  before answering and the latency is not stable. The shared HTTP client waits
  up to 300 s, so the first version of this simply hung: the trigger was pulled
  and the headset looked dead. It is now behind `OMNI_LAYOUT_MODEL=1`, still
  bounded by `MODEL_TIMEOUT_S`, and off by default.
- **Cloud speech.** 8.8 s for the same fixed line, against 772 ms for macOS
  `say`. Holding a live key made the demo *worse*. Layout's lines are a short
  fixed set, so the nicer voice bought nothing.

The honest consequence: **Layout mode on its own does not demonstrate OMNI
use.** It is the scenario and the interaction. A-mode tracking is where the
model earns its 25% -- it sees, hears, and answers in ~2.5 s. Do not claim
live reasoning for the boxes; there isn't any.

## Corner detection

Two `EnvironmentRaycastManager` wall hits, fanned either side of where the
wearer is looking, intersected on the floor plane. Normals within 40 degrees of
each other are not a corner. The floor is y = 0 because `ARSetup` pins the
tracking origin to `FloorLevel` and aborts the build otherwise.

When no walls are found the fallback frame sits 0.6 m in front of the wearer,
facing where they look. Boxes still land, still drag, still report clearance —
the arrangement is just not tied to real walls. The log says which happened:

```
QUEST_LAYOUT corner from walls at (1.20, 0.00, -0.45)
QUEST_LAYOUT corner fallback (no walls found) at (0.30, 0.00, 1.10)
```

MRUK is installed and referenced by the asmdef but deliberately unused here: it
needs Scene permission and a captured room, and two raycasts plus a fallback
place boxes either way.

## Controls

| Input | Action |
|---|---|
| Right **side trigger** (hold) | Push to talk: "fill my corner" |
| Right **A** | Open/close the furniture menu |
| Right **B** | Toggle resize mode |
| Right **index trigger** (hold) | Move a piece, resize it, or pick a menu card |
| Right **index trigger**, double tap on a piece | Rotate menu |

Note the binding in `LayoutMode`: `OVRInput.Button.Two` opens the selector and
`Button.One` toggles resize. That looks inverted against the enum names and it
is deliberate — on this headset those are what read as A and B. Measured on
device; do not "fix" it without a controller in your hands.

A and B only belong to Layout once `LayoutMode.IsArmed` — that is, after a
layout turn has placed something. Before that they are still push-to-talk and
continuous conversation, so a normal A/B run is untouched. `QuestStreamInput`
reads that flag; without it every menu press would also flip the microphone.

A pointer ray comes out of the right controller: cyan when it is on the floor,
green over a piece or a menu card, amber in resize mode.

Lock-on tries three things in the order a person expects: the piece the ray
passes through (`RayHitsBody`, an exact ray/box test in the piece's own frame),
then the piece standing on the floor point, then the nearest piece within 45 cm
of a **footprint edge**. Edge distance matters -- measured to the centre, a
1.8 m sofa refused to lock on while you pointed right beside it.

Furniture carries no colliders — nothing in this project does — so picking is
the aim ray against the floor plane, tested against each footprint rectangle.
Point at the floor where a piece stands, not at the piece in the air.

## Furniture

Six kinds (`FurnitureCatalog`): sofa, armchair, coffee table, side table,
bookshelf, floor lamp. Each is assembled from primitives by `FurnitureMesh` to
fill its w x d x h exactly, so the shape and the footprint are the same volume.

There is no mesh generator and no asset library in this repo, and a downloaded
model arrives at an arbitrary scale — the one thing this demo cannot afford,
since the footprint is the answer. Building them means true size is structural
rather than something to verify.

## Resizing and rotating

B toggles resize; hold the trigger on a piece and move your hand out to grow it,
back to shrink. Scale is uniform and anchored to the **starting** size rather
than the current size, so repeated grabs cannot drift. Clamped to 0.35x-2.5x.

Double-tapping the trigger on a piece opens the rotate menu above it: -90, -45,
+45, +90, done. It stays open between turns, because getting an angle right
takes several nudges. A double tap is the one gesture left that drag and resize
cannot swallow.

Each name plate shows the piece's live size, resized or not. `BaseSizeM` is
kept internally for the scaling anchor. The approximate-sizes caption covers the
honesty case for the scene as a whole.

## Running it

```bash
./start-demo.sh --layout
```

No SAM 2 (Layout draws its own boxes and never seeds a tracker) and no key
required. `start-demo.sh` prints which voice and which arrangement you got.

## Verification

- Offline: `cd provider && python -m unittest discover -s tests -v` — 228 tests,
  19 of them `tests/test_layout.py`.
- Unity EditMode: 98 tests, 17 of them `LayoutTests`.
- On device: the runbook's section C in `start-demo.sh`, which lists the
  `QUEST_LAYOUT` checkpoints in order.

## Known gaps

- **The model is not in the loop by default**, for the measured reasons above.
  `OMNI_LAYOUT_MODEL=1` turns it on for experiments.
- **Voice rearranging does not exist.** There is no offline speech-to-text —
  A-mode transcription is a model call and the Live gateway never sends
  `inputTranscription` — so "shift it left" cannot work with a dead key.
  Dragging replaces it.
- Clearance is between pieces only; it does not check against the real walls.
- `say_to_pcm` is macOS-only. The coordinator runs on the Mac, so this is fine
  today, but a Linux host would need a different offline voice.
- Corner detection is unproven in rooms with glass, mirrors, or clutter at
  wall height.
