# Pitch: an omni model that occupies the room

Team note, 19 Sep 2026. This is the idea we want to build for Huawei's OMNI Live track. Longer research with sources is in `docs/omni-live-research.md`. Demo props are still open. Architecture details can move; the product should not.

## The idea

Put a general assistant on a Quest 3S. It sees what you see, you talk to it, it talks back. When you ask it to *show* you something, the answer happens in the room.

Look at a mug, a cable, a poster, a chair. Ask what it is. It marks it. Say "show me how this works" or "put one here." It draws on the table, traces a path across the object, animates a rotation, or drops a ghost object onto the surface. Walk around it. Your hand covers it. Say "no, the other one" or "bigger" and the drawing moves.

The headset cannot move matter. It can make the model's reply occupy the same coordinates as matter. That is the whole trick. We are calling it pseudo-physical: world-locked drawings first, spawned 3D when the request needs volume or a missing object.

This is for everyday stuff, not a specialist assembly app. Assembly, cooking, furniture, whatever is on the table: those are things you can do with it. The product is the assistant that can put geometry into your space while you talk.

Memory stays in the stack so "that," "the other one," and "what I just said" work. It is not the pitch. A notes app plus a camera can remember. A phone chatbot cannot stick an arrow to a real connector and revise it when you interrupt.

## Why this track

OMNI Live wants a functional edge-device prototype that uses vision, speech, and language together. Cloud models are allowed. Judging is scenario 30%, omni use 25%, demo completeness 20%, interaction 15%, technical 10%.

This loop needs all three modalities on the same turn:

- Without the camera, "this" and "here" do not resolve.
- Without speech, your hands are busy and you are tapping menus.
- Without language, you get dumb labels instead of an explanation you can argue with.
- Without world-locked drawing, the model is still a voice in your head.

Judges should watch one complete interaction and understand why a text chatbot or a vision-only detector would fail it. A small, finished loop beats a pile of half-wired features. Huawei says that out loud.

Sponsored credits are CAD $40 through yibuapi, 200 keys, first come first served. Apply once: https://luma.com/0fhypcu0

## What we show

Same loop on any table. We pick the object later.

1. Look at something ordinary. Ask what it is. It answers and marks it in place.
2. "Show me." It draws or animates on or next to that object.
3. Follow-up by voice and pointing: "not that," "bigger," "from this side." The drawing updates.
4. Optional: "put one here." A 3D stand-in sits on the surface. Walk around it.
5. Your hand occludes the overlay. The drawing stays when you move.

If generated 3D is late or ugly, steps 1-3 still demo. Spawn is a bonus on the second GPU, not the thing the live path depends on.

## Why this is buildable

None of this has been run on our hardware yet. The pieces exist in official docs and samples. The remaining work is wiring, latency, and honesty when a hit fails.

### Headset: Quest 3S can give us the camera and a place to put things

Meta's Passthrough Camera API is supported on Quest 3S (Horizon OS v74+). Unity's current sample uses MRUK `PassthroughCameraAccess` and gives us:

- camera textures (documented 1280x960, plus 1280x1280 on OS v83)
- intrinsics, pose, timestamps
- left and right cameras if we want them

That is enough to send a frame to a model and later project a 2D result back into the world using the *capture-time* pose, not wherever your head is when the reply arrives.

Quest 3S also has Meta's environment Depth API, even without Quest 3's dedicated depth sensor. Minimum useful range is about 0.2 m. Depth is for occlusion and raycasting, not millimetre CAD. MRUK environment raycast is the supported way to plant content on a table or wall. Spatial anchors keep it there while you walk. None of that identifies a loose object by itself. Identification is our job.

Plan: native Unity build on the headset. Rendering, head tracking, and overlays stay on-device at framerate. Laptop talks over LAN for models. Link is fine for iteration. Demo on the device. XR Simulator does not support this camera API.

Camera feed is narrower than what you see in passthrough. Tiny screws at arm's length may be a few pixels. Cropping enlarges what is there; it does not invent threads. If we cannot hit a surface, we say so instead of floating a sticker in your face.

### Model: omni in the talk-and-see loop, tools for drawing

The prize example is Qwen omni. Two documented options:

| Model | What it does | How we would use it |
| --- | --- | --- |
| `qwen3.5-omni-flash-realtime` | WebSocket, camera+mic in, speech out, function calling | Preferred conversational path if the sponsor gateway actually exposes it |
| `qwen3.8-omni-flash` | Image/audio/video/text in, **text out only**, function calling, long context | Reasoning + scene tools, with a separate streaming TTS |

Qwen3.8 is a real model. It does not speak. Anyone pitching "Qwen3.8 Omni Flash as the voice" needs TTS on the side, which is a legitimate architecture, not a cheat. The track asks for meaningful speech/audio, not a specific speech-output API.

Realtime SDK detail that will bite us: when the model calls a tool, that turn returns tool arguments, not audio. Run the tool, send the result back, then ask for the spoken follow-up. Unity owns the drawing. The model chooses ops.

The ugly unknown: yibuapi looks like an OpenAI-compatible HTTP relay. We have not shown that the sponsored key proxies DashScope's realtime WebSocket, or even that it preserves multimodal + tool-calling fields. First hardware day: list models on the issued key, run a multimodal call, try a tool round trip, then try realtime. Do not point the sponsor key at a random Alibaba URL to "see if it works."

### Drawings: Unity renders a small scene language

The model does not emit C#. It emits validated scene ops: ring, outline, label, arrow, path, trace on a surface, diagram panel, ghost motion, load a known GLB, spawn a generated mesh.

Unity keeps those objects in the world between model turns: occlusion, undo, clear, move, resize. If a command is garbage or would cover the entire FOV, we reject it and tell the model.

Order of ambition:

1. Procedural geometry (fast, cheap, looks attached).
2. Library assets with real pivots (chair, arrow, bracket).
3. Async generated meshes for "put a thing here" when the library has nothing.

TripoSR's repo says default single-image inference is about 6GB VRAM, so it is a plausible experiment on one 4060. TRELLIS wants 16GB in the original repo. Generated meshes hallucinate backsides and have made-up scale. Fine for illustration. Not a caliper.

### Local GPUs: two 8GB cards, not one 16GB card

We have two RTX 4060s. Treat them as two separate 8GB budgets. A useful split to test:

- GPU 1: latency-sensitive grounding / tracking so "this one" does not stall speech.
- GPU 2: heavier query-time grounding, OCR, or mesh spawn so the live path stays up.

Do not load LocateAnything + SAM + a 3D generator at once on one card. Start with one grounder. NVIDIA LocateAnything-3B is the "locate anything" model; it does phrase boxes and points. Published numbers are on big GPUs. License is academic/non-profit research, commercial use not permitted except NVIDIA. Check that before we depend on it. Grounding DINO or a small YOLO-World vocab are backups.

Cloud holds the omni model. Local GPU is optional perception, not the assistant.

## What we are not building (v1)

- A memory-palace demo.
- An always-on label layer on every object (noisy, more CV than omni).
- Photoreal text-to-3D on the critical path.
- "I can read M3 vs M4 from across the table."
- Arbitrary Unity code from the model.

## What has to be true by demo day

If these fail, the idea fails in front of judges. Everything else can slip.

- Camera sample builds on our Quest 3S. Permission prompt works. We log resolution, pose, timestamp.
- A 2D point from a captured frame becomes a stable world marker while the wearer walks.
- Late replies use the old pose. Moved objects get rechecked or the overlay is marked stale.
- Omni path: audio + a frame in, speech out, plus one tool that draws something.
- Barge-in does not leave the TTS talking over you.
- One ordinary object, live, start to finish, twice in a row.

Apply for the API key immediately. Confirm Horizon OS version (v83 if we want 1280x1280). Bring a table, decent light, and objects big enough to see.

## How four people can split this

- Quest: camera, depth, raycast, overlay rendering, pointing.
- Conversation: omni session, TTS fallback, interruption, tool dispatch.
- Perception: grounder, optional tracker, crops, GPU scheduling.
- Scene language + glue: op schema, validation, demo beat, honesty states (can't see / last seen / drawing).

Agree on frame IDs, object IDs, timestamps, and tool result JSON before those lanes diverge.

If this still feels right, next step is a short design of the scene-op list and the live data path, then we pick whatever is sitting on the table for the 90 seconds.
