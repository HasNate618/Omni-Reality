# Guided tutorials

## What it is

The existing Quest camera/audio → Python coordinator → Omni → SAM 2 loop can
run a persistent, image-grounded tutorial. Hold A and ask “Show me how to
organize this desk.” One selected fresh camera frame and the recorded request
produce the entire plan. The coordinator seeds its objects together and keeps
that plan while the wearer says “next”, “done”, “repeat”, or “stop”.

Start the existing backend with:

```bash
cd provider
python -m coordinator.server --planner yibu --sam2-url ws://127.0.0.1:8766
```

Use the existing [Quest setup](quest-audio-setup.md) for SAM 2, permissions,
streaming, build and deployment. Guide creation uses A-button push-to-talk.
An active guide intercepts both push-to-talk and B-mode utterances as guide
commands. Free conversation outside a guide keeps the existing B-mode path.
The stub planner still selects a central object; deterministic three-step
examples are exercised by the offline integration tests below.

## Contract and rules

- `GuidePlan` accepts a title, 1–3 distinct physical objects, and 1–8 steps
  (the prompt prefers three). Each object has a unique ID, label, and finite
  normalized `u,v` in the selected image. World coordinates and extra fields
  are rejected. Steps have contiguous zero-based indices, instructions of at
  most 240 characters, and 1–3 ordered highlight IDs referencing those objects.
- One immutable captured JPEG and its envelope feed both Omni and SAM 2.
  Python assigns SAM 2 IDs 1..N in plan object order. There is no reseeding on
  next/repeat: all objects keep tracking even when their masks are hidden.
- `GuideSession` lives per coordinator connection, holding title, object-ID
  mapping, steps, current index, and active state. Activation waits for SAM 2
  to return a nonempty mask field for every seed object. Failure leaves no
  active guide. This does not assert segmentation accuracy or physical task
  completion.
- Active-guide audio goes to a transcript-only audited call (`guide-command`).
  Exact control phrases are classified locally; “okay, next” and “done” advance,
  “repeat” replays, “stop” terminates. Unknown speech gives a command reminder
  without changing the plan. Commands serialize in arrival order and duplicate
  utterance-end packets are ignored. No new image or full tutorial generation
  is needed for a command.
- The last advance completes the guide. Stop, clear-session, disconnect,
  tracking-origin changes, and tracker errors terminate it. Delayed transcripts
  cannot revive a cancelled guide.
- The existing `speak` message carries matching cloud speech; guide messages
  carry captions and visual state. No second TTS engine or backend is added.
  If synthesis is unavailable, captions remain available.

Messages use the existing v1 envelope/session/turn/utterance fields. A
`guide_step` payload contains:

```json
{
  "guide_id": "coordinator-generated-id",
  "title": "Organize your desk",
  "generation": 1,
  "step_index": 0,
  "total_steps": 3,
  "active_obj_ids": [2, 1],
  "instruction": "Move your mug to the right of the laptop."
}
```

The first ID is the bright primary highlight, later IDs use lower opacity,
and other objects are hidden. Unity updates cached mask layers immediately.
`guide_finished` carries `guide_id`, `generation`, `reason`
(`completed`, `stopped`, or `error`) and `instruction`. It clears the visual
state and fences late results from the finished generation. The original
tracking utterance stays pinned through command turns; tracking results still
use their original seed identity, capture pose, and stage epoch.

## How to verify

From `provider/`, with its requirements installed:

```bash
python -m unittest tests.test_guide tests.test_guide_flow tests.test_tracking -v
python -m unittest discover -s tests -v
```

The integration suite runs the real coordinator against a local SAM 2 socket
double. It proves one plan and one multi-object seed across three steps,
repeat, unknown commands, live-mode command routing, continued hidden-object
tracking, completion, stop/restart, duplicate ends, concurrent commands,
cancellation, tracker errors, stage changes, and clear-session cleanup.
It spends no model credit and does not load SAM 2 weights.

Unity EditMode tests in `GuidePresentationTests` cover message routing,
original tracking identity, active mask emphasis, and completion fences.
They require a Unity editor. On Quest, verify:

1. Ask for a three-step desk guide with three visible objects.
2. Check primary/secondary masks and hear the same captioned instruction.
3. Say “repeat”, then “okay, next”; the old primary hides and the next appears.
4. Move the headset and an inactive object, then advance to that object;
   its highlight should follow the current tracking mask.
5. Say “done” through completion; check all highlights clear. Start another
   guide and verify spoken “stop” and controller X both clear it.
6. Disconnect SAM 2 or reset the tracking origin; ensure no stale guide remains.

Model grounding, physical headset alignment, audible speech timing, and a real
SAM 2 GPU stream remain hardware/live checks; offline tests cannot prove them.

Verification recorded on 2026-09-20: `/tmp/omni-guide-test-venv/bin/python -m
unittest discover -s tests -q` from `provider/` passed **229 tests**, including
12 guide-flow integration tests. `compileall` for changed Python modules and
`git diff --check` passed. Five Unity EditMode tests were added but not run:
no Unity editor is available in this environment. No live gateway call, real
SAM 2 inference, APK build, or headset check was performed.
