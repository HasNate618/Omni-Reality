# Cornerbuilt — try furniture in your room before you buy it

**Huawei OMNI Live track submission.** A Quest 3S spatial assistant for the
empty-corner problem: photos lie about scale, measuring tape doesn't show
*together*, returns cost money and weekends. Show it your cart, say "fill my
corner," and life-size stand-ins of your listings appear on the floor.
Rearrange them with sentences, fit-check against the real corner, save the
layout — hands-free, in the room.

> 🎥 **Demo video (5:00 max):** [link] — 2–3 min of the room filling up,
> remainder explaining how it works.

## Why this, why omni

A phone shows you pictures *of* furniture. Cornerbuilt puts furniture-shaped
answers *in* your room, at true size, while you argue with them. That needs
seeing, hearing, and talking on the same turn: the camera reads your cart and
your corner, speech keeps your hands free to walk the space, language resolves
"swap those," "shift left," "too crowded." A chatbot has nothing to point at;
a vision-only detector can't discuss, revise, or refuse. The trace of work —
boxes that land, meshes that fill them, numbers spoken with tolerances — is
the product.

## What the video shows

1. **Read the cart** — cart page held to the camera; agent parses items with
   listed dims and plans the lift order aloud.
2. **Fill the corner** — true-scale ghost boxes land instantly; generated
   meshes swap in async while conversation continues.
3. **Art-direct it** — "swap the lamp and table," "shift left," "lose the
   speaker." Same drawings revised; walk-around holds.
4. **Fit-check** — "does the table fit that corner?" Measured live, math spoken
   with labeled tolerance.
5. **Save it** — thumbnails, dims, prices. "Same loop, any corner."

## Honesty contract

- Scale comes from listings the camera read — never guessed, never invented.
- Mesh fronts come from catalog photos; backsides are disclosed as imagined.
- Measurements carry ±2cm tolerance, on screen and in speech.
- Meshes never stretch to fill: a misproportioned mesh keeps its shape and the
  box stays visible.
- What the system can't verify (electrical state, load, exact fit) it refuses
  out loud, with the check to run instead.

## How it works

```
Quest 3S (Unity)              Laptop (Python)               Cloud / local GPU
─────────────────             ─────────────────             ──────────────────
camera · mic · pointing       coordinator: session,         omni model (vision +
depth · raycast · anchors      turns, tools, audit           speech in, text +
render · occlude · animate     WebSocket JSON                tool calls out)
placement ACK ─────────────────────────►                    TTS voice out
                              SAM2 mask → TripoSR mesh (idle 4060, async)
```

- The model decides *what* to show; the headset decides *where* it can go.
  The model never emits coordinates or code — only semantic ops
  (`mark`, `ghost`, `connect`, `place_listing`), validated against JSON Schema.
- The first thing planted per item is a **listed-size box** (the promise);
  the mesh fills it uniformly without stretching (the illustration).
- Capture-time pose anchors every drawing — never the live head pose — so
  overlays survive walking, occlusion, and slow networks.
- Barge-in tombstones kill cancelled turns; late packets never redraw.
- The API key lives in `YIBU_API_KEY` on the laptop only. Unity never calls
  the cloud; the audit ledger redacts keys, prompts, and media.

## Repo layout

- `QuestDemo/` — Unity 6 LTS app (passthrough, spatial ops, ghost motion,
  honesty chips). Build guide: `docs/questdemo-build.md`.
- `provider/` — gateway client, coordinator (session/turns/planner), protocol
  schemas, voice adapters, SAM2 + generation workers. Contract:
  `docs/omni-provider-api.md`.
- `docs/` — one doc per topic; start at `demo.md` (locked demo run-of-show),
  then `docs/superpowers/specs/2026-09-20-generated-furniture-scale-design.md`
  (scale-truth binding spec).
- `sam2/` — streaming segmentation service; setup in
  `docs/omni-sam2-streaming.md`.

## Run it

Prereqs: Unity 6 LTS + Android Build Support (see `docs/questdemo-build.md`),
Python 3.14, Quest 3S on Horizon OS v74+, demo LAN.

```bash
# 1. Headset: build & deploy (NixOS FHS shell)
nix run .# -- -c '<licensing-wrapped batchmode build>'   # → QuestDemo/Builds/
adb install -r QuestDemo/Builds/QuestDemo.apk

# 2. Laptop: coordinator (canned fallback needs no key)
cd provider && . .venv/bin/activate
python -m unittest discover -s tests -v        # offline, no credit
YIBU_API_KEY=... python server.py              # live path (optional)

# 3. Point the camera at a cart page, say "fill my corner."
```

No-network fallback: the canned planner replays the locked beats with zero
live calls; already-placed drawings stay put and the app reports `offline`.

## Status & limits

Built and green: conversation loop, tool calling, placement, ghost motion,
honesty states, audit (200+ offline tests). Validated: protocol + taller.
Proving next: on-device floor placement, pre-baked mesh quality. Out of scope
by design: clothing try-on, batch reveals, live listing-parse, grab tracking,
gen meshes on any live path. Approximation is always labeled; refusals are
spoken.

Team [names] — Hack the North 2026, Huawei OMNI Live.
