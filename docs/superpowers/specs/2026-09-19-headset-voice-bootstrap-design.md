# Headset voice bootstrap: binding design

Date: 2026-09-19.

Status: binding for the first controller-free headset conversation. It narrows
`2026-09-19-voice-spatial-omni-integration-design.md` for this bootstrap only.
It deliberately omits spatial capture and placement. The original voice-spatial
contract remains binding for later grounded turns.

## 1. Goal

A wearer can put on Quest, speak naturally without a controller, and hear a
cloud Omni response through the headset. This milestone proves microphone
capture, bounded transport, coordinator turn ownership, cloud reasoning,
cloud PCM playback, and captions. It does not claim spatial grounding.

## 2. Explicit exclusions

The bootstrap sends no JPEG, capture envelope, ray, depth sample, gaze point,
or scene operation. It creates no marker, label, ghost, generated object, or
procedural object. The cyan aim line is hidden without a tracked controller;
the demo cube is removed from the headset build. Existing controller bindings
remain developer-only fallbacks and are not required for the demo.

Spatial turns will resume only after this bootstrap works, through a distinct
mode that attaches the one fresh capture required by the voice-spatial design.

## 3. Controller-free turn lifecycle

1. On app activation, Quest requests Android `RECORD_AUDIO` permission once.
   This uses the system permission dialog only; there is no custom consent UI
   or settings workflow.
2. Once granted, Quest records 16 kHz mono microphone audio into a local
   rolling pre-roll buffer of 300 ms. No audio leaves the device yet.
3. RMS at or above `0.01` opens an utterance. Quest sends the pre-roll followed
   by 100 ms signed-16-bit little-endian PCM chunks over the existing WebSocket.
4. RMS below `0.01` for 800 ms closes the utterance with `utterance_end`.
   An 8 s maximum also closes it. The coordinator's existing 0.5 s minimum
   still drops too-short turns.
5. The coordinator issues `turn_started`, runs a voice-only planner turn, then
   sends `speak` with text and 16 kHz mono `pcm_s16le` audio. There are no
   scene-op ACKs in this mode, so it does not use the spatial ACK barrier.
6. Quest shows the caption and plays the PCM through `SpeakCloudPlayer`.
   While audio plays, Quest does not open another utterance. This is bounded
   half-duplex conversation, not full duplex or barge-in.
7. On silence, socket loss, microphone failure, or an invalid live response,
   all local buffers clear. No delayed replay occurs.

## 4. Planner modes

### Offline transport proof

`--planner voice-stub` accepts audio-only turns and returns a deterministic
caption plus a short non-speech 16 kHz PCM test tone. The tone must be visibly
and audibly documented as a transport check, never represented as Omni speech.
It spends no provider credit and has no scene operations.

### Live conversation

`--planner yibu --voice-only` sends bounded raw PCM through the documented
provider HTTP helper without an image or tools. It produces response text, then
uses the existing cloud-speech adapter to synthesize the required playable PCM.
All calls have a short `--purpose` label and use the existing redacted audit
ledger. `YIBU_API_KEY` remains laptop environment only; Unity, logs, and
protocol messages never receive it. The approved live smoke is minimal and
occurs only after the offline transport proof.

If either cloud leg fails, Quest may show the returned honest caption but the
voice bootstrap is not accepted as a conversation pass until valid cloud PCM
plays.

## 5. Ownership and privacy

Quest owns local VAD, pre-roll, maximum duration, audio capture, transport,
and playback. The coordinator owns turn IDs, audio assembly, planner calls,
audit, and speech synthesis. Only audio within an opened bounded utterance
leaves Quest. Buffers are in-memory and clear on terminal paths. No keyword
model, camera, image, depth sample, transcript store, or continuous cloud
recording is part of this scope.

## 6. Verification

Offline checks require Unity EditMode tests for VAD onset, pre-roll retention,
silence closure, and max-duration closure, plus provider tests for audio-only
turn acceptance and voice-stub PCM shape. The existing provider suite must
remain green.

The device transport proof passes only when a headset wearer speaks without a
controller, the coordinator receives a bounded utterance, and Quest audibly
plays the stub tone while showing its test caption.

The approved live smoke passes only when a short spoken headset utterance
reaches the audio-only Omni planner, a redacted audit record has the declared
purpose and no media/prompt/reply/key material, and Quest audibly plays cloud
PCM with its matching caption. This is a voice-only pass, not a spatial or
omnimodal grounding pass.
