# Demo: Layout Mode — fill an empty corner from your cart (LOCKED)

Status: locked 2026-09-20. This is the demo. Format: **prerecorded video, 5:00 max.
2–3 min showing what it does, remainder explaining how.**

## One line

Empty corner + online cart → furnished layout you art-direct with sentences.

## Scenario (rubric: specific + real)

Renter/furnisher with an empty corner and a cart of 3 listings. Problem: photos
lie about scale, scrolling listings while standing in the room is useless, returns
cost money. "Will this fit, and will I like it together?" — answered life-size,
in the room, before buying.

## What it does (the 2–3 min show) — voice vs visual

The pipeline runs silent; the agent narrates. Every beat below pairs the
VOICE track (what the agent says, streaming over the work) with the VISUAL
track (what the room shows). Hands build; the voice directs, argues, admits,
remembers.

1. **Read the cart (0:00–0:30).**
   - VOICE: "Three items — arc lamp, side table 55 by 55, speaker. Glass top on
     the table, so I'll lift that one last. Fill your corner?"
   - VISUAL: cart page held to camera; item queue overlay (3 chips with dims).
   - Capability: vision + language disambiguation; gen order planned aloud.

2. **Fill the corner (0:30–1:15).**
   - VOICE: "Boxes first — true to listed size. Fronts from your photos, backs
     imagined. Textures landing one by one, keep talking."
   - VISUAL: 3 true-scale ghost boxes land (procedural, zero GPU); meshes swap
     in async per item. Walk-around holds throughout.
   - Capability: orchestration + narration over async work; degradation
     announced, never a silent spinner. If a mesh fails: "speaker's being
     shy — box is exact, texture coming."

3. **Art-direct it (1:15–2:15).**
   - VOICE: "Swapped." / "Left 20 centimeters — more?" / "Speaker's out.
     Room breathes better."
   - VISUAL: same drawings revise in place (swap, shift, remove); barge-in
     kills the old turn mid-sentence, late ops never redraw.
   - Capability: spatial language → revise ops; interruption discipline.

4. **Fit-check (2:15–2:35).**
   - VOICE: "55-centimeter listing, 63-centimeter corner — fits with 8 to
     spare." Pushed ("are you sure?"): re-measures, careful register.
   - VISUAL: two-ray measure ticks + tolerance chip (±2cm, labeled).
   - Capability: measurement + arithmetic + calibrated uncertainty.

5. **Save it (2:35–2:50).**
   - VOICE: "Saved — thumbnails, dims, prices. Same loop, any corner."
   - VISUAL: take-home card overlay; layout holds as camera pulls back.

## Explanation (remaining ~2 min)

- **How (60–90s):** cart vision → item queue (`catalog | user_photo | live_capture`
  per item) → ghost boxes now, TripoSR meshes async on the idle 4060 → revise
  ops on known `drawing_id`s. Capture-time pose, tombstoned barge-in,
  key-never-in-Unity. Headset renders at framerate; nothing waits on the network
  to stay stable.
- **Honesty (20s):** scale from listings, backsides imagined, measurement ±2cm,
  "I can't verify" refusals. Ghosts are rehearsal, not purchase.
- **Image sources:** catalog pixels direct (best evidence). SAM2 cutout only for
  user photos / live-captured objects (`sam2ws`). Never photo-of-screen → gen.
- **What's next (20s):** same loop — TV-cable nest, workbench, comms closet.

## Production plan

- **Pre-bake:** cart parse cache, 2–3 meshes night before, exact corner measured.
- **Live in video:** voice layout verbs, fit-check measure, walk-around, revise.
  Max one live gen attempt; boxes carry the demo if it fails.
- **Cut list:** clothing/mirror try-on (no body tracking, fit claims dishonest),
  batch-all-at-once reveal (latency), live sheet-parse (pre-cache), grab-to-drag
  (unbuilt — voice-move via revise ops instead), `place_generated` on any live
  path.
- **Props:** empty corner (taped), printed cart page, Quest 3S + charger, laptop +
  hotspot, lens wipes.
- **Fallbacks:** mesh fails → boxes carry it. Measure fails → "need you closer"
  chip. Anything dies → canned StubPlanner sequence, same beats.

## Rubric mapping

- Scenario 30: empty-corner furnishing, pre-purchase scale blindness, returns.
- Omni 25: cart vision + voice layout + narrated fit + tap/mic optional — fused
  per turn.
- Completeness 20: one corner, filled, fit-checked, saved. Small, finished.
- Interaction 15: every sentence rearranges the room; interrupt/revise free.
- Technical 10: listed-dims scale truth, labeled tolerance, imagined-backside
  honesty, capture-time pose, tombstones, privacy (key in laptop env only).
