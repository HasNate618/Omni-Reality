# Generated furniture at listed scale: binding design

Date: 2026-09-20.

Status: binding for the next implementation slice on branch
`spatial-model-unity-integration`. It narrows
`docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md` (parent
spec) and `docs/superpowers/specs/2026-09-19-voice-spatial-omni-integration-design.md`
(voice spec) to one question: how a page-stated product size becomes a
life-size object in the room. Where this spec is silent, the parent spec,
the voice spec, `docs/omni-provider-api.md`, `docs/omni-worker-tools.md`, and
`docs/omni-spatial-loop.md` still bind. Where they conflict on generated
furniture scale, this spec wins.

The demo this serves is `demo.md` (untracked in this worktree): prerecorded,
5:00 max, Layout Mode — fill an empty corner from a listing. That document is
locked; this spec implements it and does not reopen its scenario.

Hardware and live provider calls are unrun on this branch. §9 names what must
still be proven on device.

## 1. The problem

Generated meshes do not currently arrive at a usable size, and nothing in the
protocol lets a stated product size reach the headset.

- `GeneratedMeshPlacer` pins every import with `FitInsideUnitSphere`: a 0.55 m
  side table and a lamp both land inside a 1 m sphere, so the object is a
  guess, not the product.
- A failed import fell back to a placeholder cube, a second, smaller guess
  (pre-slice state; §6.3 removes this fallback).
- `place_generated` carries no extents, so no op can state a size.
- `ghost` is a fixed 0.12 m marker (`GhostLabelConnect.GhostSizeM`); it is a
  pointer, not a footprint.
- `place_procedural` is capped at 0.40 m per element inside a 1.0 m bounding
  sphere and composes a row of diagram parts. It is not furniture.
- Generated roots are not registered in `DrawingStore`, so `remove`, `undo`,
  and the clutter cap do not see them.
- The model may not emit `place_generated` or `place_known` at all
  (`docs/omni-worker-tools.md`), and it may never emit world coordinates.

The result is a demo that can put *something* in a corner, not the thing the
wearer was about to buy.

## 2. Product claim

A size that a page states is the object's size in the room. The wearer sees a
listed-size box where the product will stand, and the generated mesh fills that
box rather than defining it.

The floor plan of the interaction:

1. The wearer looks at a page that states a product and its dimensions.
2. Omni reads name and W×D×H from that frame into listing memory.
3. The wearer asks for it in the room ("fill that corner with these").
4. Omni calls `place_item` with the listing and the sizes it read.
5. The coordinator validates the metres, authors `place_generated`, and Quest
   plants a listed-size box on the capture-time hit.
6. The mesh arrives and fits inside the box, uniform scale, proportions kept.
7. The wearer art-directs by voice and by moving objects on the floor.

A listing whose sizes are unknown is never generated. The model asks instead.
An approximate mesh that does not match the box is shown as approximate, and
the box stays the claim.

## 3. Decision: extend `place_generated` (option B)

Three shapes were considered.

**A — a new layout-slot object kind.** One Quest object per item holding a
listed AABB plus an optional mesh child, with generation only swapping a mesh
into an existing slot. Cleanest model of the product; adds an op kind, a
lifetime, and a second placement vocabulary.

**B — put extents on `place_generated`.** The existing generation op gains
`extent_m`, fits into an AABB instead of a unit sphere, and its root becomes
grabbable. Smaller protocol change, reuses the existing op, job, and ACK path.

**C — stretch procedural cubes to metres.** Reuses `revise_procedural`, but the
procedural factory is a closed 1 m grammar for diagrams. Furniture-sized cubes
in it fight the clutter cap and the element grammar.

**B is chosen.** The cost is accepted: there is no slot that survives a failed
generation at full size unless the AABB itself is the fallback, which §6.3
requires; and pack, grab, and revise all key off one `drawing_id` rather than a
slot identity.

## 4. Claimed sizes: listing memory and evidence

The model may state metres only as an **object's own dimensions**, and only
when it read them from a frame it saw or heard them from the wearer. It still
never emits world coordinates, poses, quaternions, or C#.

**Listing memory** is coordinator-side working memory:

```
{ name, extent_m: [w, d, h], source_frame_id, source: "page" | "spoken" }
```

- Any frame that shows a product with dimensions may contribute rows: a cart
  page contributes several at once, a single product page contributes one.
- The only frame source is the Quest camera. The laptop does not screenshot
  its own screen; there is one vision source, and it is what the wearer sees.
- Rows are **recorded when a listing is placed** (§5.2), keyed by normalized
  name. The model states the sizes it read; the coordinator validates and
  remembers them. Nothing else populates memory in this slice.
- A listing with no stated sizes on any axis is **not** placeable. The model
  asks the wearer instead. A spoken answer is a valid source.
- Rows are session-scoped and cleared with the session, like inspect objects
  and jobs.

Axis convention for `extent_m` is the listing's own frame, not world space:
`w` across the front face, `d` front-to-back, `h` vertical. Mapping to the
placement frame is §6.2.

## 5. Protocol changes

### 5.1 `place_generated` gains `extent_m`

`provider/protocol/schemas/scene_op.json`:

- New optional property `extent_m`: array of exactly 3 numbers, each ≥ 0.05 and
  ≤ 3.0.
- New optional property `offset_m`: a number between −3.0 and 3.0, the
  frame-relative distance along the placement frame's width axis that this
  item's centre sits from the first item's centre (§7.1). It is metres in a
  local frame, the same category as `extent_m`, which is why it may be on the
  wire while `world_point` may not.
- When present, it is authoritative for the planted object's box.
- When absent, behaviour is unchanged (unit-sphere fit). This keeps existing
  fixtures and the shop-to-life path working.

The field is coordinator-authored. It is **not** added to
`model_scene_op.json`: the model still cannot emit `place_generated`.

### 5.2 New model tool `place_item`

`provider/omni/tools.py` gains one tool:

```
place_item(name, extent_m, target)
```

- `name`: 1–40 chars, what the item is ("oak side table", "warm floor
  wash"). Rows are keyed by the normalized name so a repeat placement updates
  rather than duplicates.
- `extent_m`: **required** array of exactly 3 numbers, each 0.05–3.0 m. It is
  required so that nothing can ever be placed without stated numbers: the model
  must state what it read, or ask the wearer, or place nothing.
- `target` uses the existing target grammar (image-space only). No world point.
- Refused when `extent_m` is missing, mistyped, or out of range, and refused
  when the turn has no capture frame to anchor to — the same condition every
  other frame-anchored op already has.

The tool is deliberately named for the *act* of placing, not for where the
sizes came from. A page-stated listing is one evidence source; a curated
suggestion entry carrying its own size (§11) will be another. Both end in the
same placement mechanics, so both call the same tool and only the evidence
source differs. This slice implements the page-stated path only.

There is no separate "record an item" tool. Recording happens as a side
of placing, which removes a whole class of "unknown listing_id" failure and
keeps the evidence attached to the act that needs it.

The coordinator turns an accepted `place_item` into a coordinator-authored
`place_generated` carrying `extent_m`, the job, and the target. Refusal
reasons reuse the existing vocabulary rather than inventing strings.

### 5.3 Job record carries extents, and extents jobs plant early

`provider/coordinator/jobs.py`: the job record gains `extent_m` and
`planted`.

`place_generated` is emitted from two places, and which one applies depends
on whether extents are known:

- **Extents present (this slice's path).** The coordinator emits
  `place_generated` as soon as the job is accepted and marks the job
  `planted`. The wearer sees a stated-size box while the mesh is still
  baking. When the job later reports ready, `on_job_terminal` must **not**
  emit a second op for it — Quest is already fetching.
- **No extents (existing path).** Behaviour is unchanged: nothing is emitted
  until the artifact is ready.

Generation is serialized: `session_generation_busy` refuses a second worker
job while one is queued or running. The rule protects the worker, so it applies
**only when a worker is actually about to be queued** — the pre-baked path
queues nothing and is not subject to it. A refusal must leave no trace: a
refused placement records no row and shifts no pack offset. A demonstration
that places several listings therefore cannot generate them concurrently. This
slice adds a **pre-baked artifact registry**: a listing name that maps to an
existing artifact id is placed immediately without queueing a worker at all.
Whether a listing is pre-baked is coordinator configuration, not a model input.

The consequence for the live path is deliberate: while one live generation is
queued, a second live placement is refused `busy` rather than silently
co-queued. The worker's own `BusyError` remains the backstop, so even a
misconfigured coordinator that binds no queue function degrades to `busy`
rather than double-queuing.

### 5.4 ACK vocabulary is unchanged

Quest ACKs `placed` with `drawing_id` and `pin: "surface"` at box-plant time.
`placement_ack.json` already requires exactly that for `placed`. Planting a box
before its mesh exists therefore needs no new status, no new reason, and no new
message type. The mesh arriving later is not an ACK event.

This interacts with the existing rule that speech after generation waits for
placement ACKs and must not claim the GLB is already placed. The honest
sequence becomes: ACK the box, say the box is in the room at the stated size,
and say the mesh is still baking until the job reports ready. §8.2 pins the
wording obligation.

## 6. Quest rendering

### 6.1 One root per placement

Each accepted `place_generated` creates one root GameObject owned by
`DrawingStore` under its `drawing_id`, with:

- a translucent **listed box** child at `extent_m`, and
- a **mesh** child, empty until the GLB lands.

Registering the root in `DrawingStore` is required, not optional: it is what
makes `remove`, `undo`, the clutter cap, and `revise` see generated furniture.
Today they do not.

### 6.2 Placement frame

The box is planted on the existing capture-time hit path — the same
`CaptureGeometryCache` entry, same range, same stale rules as a `mark`.

Because the anchored case is a floor or table top, the hit normal is
approximately gravity up and cannot supply all three axes on its own. The box
therefore takes **height along gravity up**, and both width and depth lie in
the horizontal plane, oriented by the **capture-time camera forward** flattened
to the horizontal: width across the wearer's view, depth running away from
them. The box rests on the surface at the hit point rather than centring on it.

Note the direction convention, because it is easy to read backwards. The box's
forward runs *along* the capture view — away from the wearer — not toward them.
That is deliberate: it puts the object's width across the wearer's view and its
depth away from them, which is what makes a non-uniform box read as furniture
facing you, and it leaves the box's local right axis equal to the wearer's own
right. Positive `offset_m` and "shift it right" are therefore both
wearer-relative. Flipping the sign would invert left and right for the very
interaction the demo uses. No world point is ever sent to the
model; the frame exists only on Quest.

### 6.3 Mesh fit

When the GLB arrives, its bounds are fitted **uniformly** inside the listed
box: one scale factor, `min(box_extent / mesh_extent)` per axis, never per-axis
stretch. The mesh may therefore not fill the box.

Stretching to fill is rejected. A stretched mesh would make a misproportioned
generation *look* correct while being a lie about the object, which is the same
failure as the unit-sphere fit in a different costume.

If the mesh's aspect ratio differs from the listing box by more than 25% on any
axis, the box stays visible and the honesty path in §8.1 speaks.

The GLB fetch is bounded: six attempts at roughly 15 s spacing, aborting early
on a terminal worker failure. This is the only window in which a mesh may
arrive late.

If the import fails, the retries exhaust, or the payload exceeds
`MaxGlbBytes`, the **listed box remains at full stated size**. The 0.12 m
placeholder cube is no longer the failure mode for `place_generated`.

## 7. Packing and interaction

### 7.1 Packing rule

Items pack along the horizontal width axis of the placement frame (§6.2), the
direction the row of objects runs across the wearer's view. The **first item
is centred on the hit**, so a lone placement lands where the wearer pointed;
each later item's centre offsets from the first by the widths between them
plus a 0.05 m gap, sharing one floor height and one facing. Offsets are
relative to the first item, and the coordinator states each one as
`offset_m` on the op.

The arithmetic is a pure function of the ordered listing extents, unit-tested
without Unity. The coordinator owns it. The model speaks layout language
("fill that corner", "shift it left") and never computes or receives world
metres.

If the packed run exceeds the wall, this slice does not claim otherwise. It
reports the run length. A numeric fit verdict needs the two-ray measurement in
§11.

### 7.2 Grab

Generated roots carry an XRGrabInteractable, constrained to translation on the
floor plane:

- X and Z follow the hand.
- Y is locked to the planted surface height. Objects are not lifted.
- Scale is locked. Grab cannot fight the listed size.
- Rotation is voice-only in this slice; grab does not yaw.
- Release keeps the final pose. The drawing stays world-locked.

Interaction SDK wiring is new to this project: `QuestDemo/Packages/manifest.json`
carries the XR packages but no scene currently uses a grab interactable.

### 7.3 Voice revise

`revise_procedural` gains generated drawings as valid targets, addressed by the
same `drawing_id`:

| Verb | Generated furniture |
| --- | --- |
| nudge / move | allowed, floor-plane offset |
| rotate | allowed, yaw only |
| remove | allowed; clears root, mesh, and job reference |
| swap | allowed; re-targets the box to another listing's extents and mesh |
| enlarge / shrink | **refused** — the listing is the truth |

Refusal reasons reuse the existing vocabulary rather than adding new strings.

## 8. Honesty obligations

### 8.1 Approximate mesh

When §6.3's 25% threshold trips, the box stays and the wearer is told the mesh
is approximate. The existing honesty-chip surface carries this; it does not get
a new screen-centre HUD.

### 8.2 Speech ordering

- The object may be spoken about as soon as its box ACK lands: a box at the
  stated size, in the room.
- The coordinator does not separately assert that the mesh downloaded. Quest
  sends no arrival report in this slice (§11), so "the mesh is on your head"
  would be unverifiable from the laptop. It describes the object; it does not
  narrate a transfer it cannot see.
- A mesh that never arrives is reported as a mesh that never arrived. The box
  is still the answer to "will it fit".

### 8.3 Never

- Never a size the model did not read or hear. No guessed metres.
- Never a claim of millimetre fit, structural load, or fastener size.
- Never a stretched mesh presented as the product.
- Never a world coordinate, pose, or quaternion leaving the laptop.
- Never `YIBU_API_KEY` in Unity, logs, the repo, or a doc.
- Never cost arithmetic in the tooling.

## 9. Tests

Python, offline, no credit (`cd provider && . .venv/bin/activate && python -m unittest discover -s tests -v`):

- `extent_m` accepts a 3-number array in range; rejects missing length, 2 or 4
  numbers, non-numbers, negatives, and values outside 0.05–3.0 m.
- `offset_m` is optional and bounded at ±3.0 m.
- `place_generated` without `extent_m` still validates, so existing fixtures
  and the shop-to-life path are unchanged.
- `place_item` is refused with `extent_m` missing, with the wrong number
  of axes, with a non-number, with a negative value, with any axis outside
  0.05–3.0 m, and with a turn that has no capture frame.
- `place_item` succeeds with valid extents, and the resulting
  coordinator-authored `place_generated` carries `extent_m`.
- Placing the same name twice updates one row rather than creating two.
- An extents job emits exactly one `place_generated`: at accept, and not again
  when the job turns ready. A job without extents still emits only at ready.
- A pre-baked listing resolves to its configured artifact without queueing a
  worker.
- Pack arithmetic: a lone item's offset is zero so it lands on the hit;
  later items clear the widths between them plus 0.05 m gaps; offsets are
  monotonic and never overlap; a run length matches the inputs.
- Job record retains `extent_m` across the queued → ready transition.

Unity EditMode, no headset:

- Uniform fit for a non-cubic box: a 0.55 × 0.40 × 0.72 m box and a
  differently-proportioned mesh produce one scale factor, and the mesh's
  proportions are unchanged after fitting (the anti-stretch assertion).
- The 25% aspect threshold trips the approximate path, and just under it does
  not.
- A failed or oversized import leaves a listed-size box, not a 0.12 m cube.
- The generated root is registered under its `drawing_id`, and `remove`,
  `undo`, and the clutter cap then see it.
- Grab contributes XZ motion only: Y and scale are unchanged, and release
  preserves the pose.
- `revise_procedural` on a generated `drawing_id` applies nudge, rotate,
  remove, and swap, and refuses enlarge and shrink.

Device gate (manual, not automated): plant a listed box from a real page, fit a
real GLB at a measured size, grab it across the floor, and confirm the box and
mesh do not drift while the wearer walks.

## 10. Acceptance

This slice is done when, on device, a page-stated size becomes a life-size box
in the room, the mesh fills that box without stretching, the object can be
moved on the floor by hand and revised by voice, and the wearer is told the
truth in every failure path above.

## 11. Out of scope

- Two-ray corner measurement and a numeric fit verdict. This slice reports
  packed run length only. The locked demo's fit-check beat stays open until a
  follow-up slice, and that slice should own the wall measurement, the
  tolerance chip, and the comparison copy.
- Generating from a listing photo URL. The artifact source for this slice is
  the existing worker; a listing photo is not yet a generation seed.
- Hand-tracking grab. Controller grab only.
- A first-class layout-slot object (option A). Revisit if generated furniture
  needs a lifetime independent of its op, or if swap must preserve a slot
  identity across failed generations.
- Multi-session listing memory, catalog sync, or price handling.
- A Quest-side mesh-arrival report. Until one exists, §8.2 forbids asserting
  that a transfer completed.
- **A curated suggestion inventory.** Injecting per-turn design taste
  (layered lighting, textile warmth, greenery, cable concealment, wall
  balance) with triggers and phrasing variants, so "I think something is
  missing" gets a diagnosis instead of a recording. Its own slice. The rule
  that slice must keep: the curated entry carries the **size**, so the model
  reasons about *which* suggestion fits without inventing metres — the same
  evidence discipline as §4. Note also that several such suggestions are light
  rather than geometry, and are better served by an existing glow or ghost op
  than by a generated mesh.
- **Image-less generation (text→image→3D).** The worker today is image→3D only:
  `workers/gen_client.queue_job` requires a `jpeg_b64`. The parent spec already
  names the extension — "a later text-to-image then image-to-3D step. Not
  text-to-3D by itself" — so this is a text-to-image model in front of TripoSR
  at the worker layer. It is what would let an item be created with no
  reference image at all. Note the sizing consequence: an item with no listing
  and no measured surface (a textured wall is the obvious case) has no honest
  size until the two-ray measurement above exists.
