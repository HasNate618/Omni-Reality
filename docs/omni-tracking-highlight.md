# Omni tracking highlight

The model highlights what the wearer asks about: "show me the X" → a green
mask tracks the object on-device for 10 s, then fades. One tool, one object,
no removal — a replaced track supersedes the old one.

## What it is

- Live function call `highlight_object(label, u, v)`: declared in the Live
  session setup (`HIGHLIGHT_DECL` in `provider/coordinator/live_turn.py`),
  silent (no audio), answered with a tool response so the model keeps talking.
- `provider/coordinator/sam2_bridge.py`: Quest frames → SAM2 click protocol.
  One bridge per connection, pinned seed + bounded CPU JPEG history, 10 s TTL
  measured from seed. Ported from the sam2-tracking-adoption worktree.
- `TrackingMaskOverlay` (Quest): camera-facing quad, holds 10 s, fades
  0.75 s, hides. New results replace; stale generations never paint.

## Contract / rules

- u/v are fractions across the **question photo** the model saw. Pixels are
  accepted defensively and normalized when the photo size is known
  (`parse_highlight_args`); anything else fails honest, never seeds.
- The bridge seeds only on the in-flight turn's question frame
  (`state._live_frame`); tombstoned turns never seed. A barge-in or any new
  turn stops a live track first; a seed interrupted mid-flight stops the
  bridge and answers `Interrupted.`
- `begin()` starts a fresh frame history (a retry must not trip the
  out-of-order reject). Coordinates normalize per axis (mixed
  fraction/pixel pairs don't corrupt the good axis).
- The tool answer is optimistic (`Selecting …`): SAM2 may still fail
  seconds later and the status carries the verdict.
- Quest streams ~5 fps small frames (`payload.tracking=true`, no utterance)
  **only while a track is live** (`tracking_status` selecting/initializing/
  tracking). `stopped`/`error` hides the overlay and stops the stream.
  Disconnects reset streaming state; an open utterance pauses the stream so
  the question frame always wins the capture gate. Results whose envelope
  stage moved on are dropped, never painted.
- TTL is server-authoritative (bridge stops, status `stopped`); the overlay
  clock only ever hides early on loss, never extends a track.
- A stage change resets the bridge (origin moved) with an honest status.
- Server needs `--sam2-url ws://GPU-BOX:8767` (SAM2 video server). Without
  it, no bridge exists and tool calls answer "unavailable" — the voice loop
  is unaffected.
- Redaction holds: logs carry states/counts/dims only. `track_state` is an
  allowlisted enum in `VoiceBootstrapLog`. No masks, labels-as-text beyond
  the short tool label, or JPEGs in logs.

## How to verify

- Offline: `cd provider && .venv/bin/activate && python -m unittest
  tests.test_tracking -v` (fake SAM2 socket double, no GPU/cloud).
- Unity headless: EditMode suite covers status/result parsing, 10 s hold +
  fade + hide, replace restarts the clock, stale generations dropped.
- Live (needs GPU box + credit): run with `--sam2-url`, ask "show me the
  X", expect `highlight_tool_called` → `tracking_status tracking` →
  `tracking_result`s for 10 s → `stopped`; quad greens the object then fades.
