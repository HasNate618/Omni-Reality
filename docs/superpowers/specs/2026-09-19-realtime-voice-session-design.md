# Realtime voice session: binding design

Date: 2026-09-19.

Status: binding. Supersedes the HTTP-stitch turn loop of
`2026-09-19-perception-voice-qa-design.md` (one-shot Omni HTTP +
one-shot TTS) for live conversation. That spec's transport, capture,
redaction, audit, and credit rules still bind except where this spec
says otherwise. Measured evidence: warm Gemini Live turn 1.24 s with
audio (vs 10–12 s one-shot), image turn 3.4 s correct, server
`INTERRUPTED` + "Okay." on barge-in; Qwen realtime cannot hear
(audio_tokens 0 / connection drop), so it is out.

## 1. Goal

A wearer holds a spoken conversation with what the headset sees:
replies start within ~2 s of silence, interruptible by voice at any
time, no controller, no per-turn dial-up. One persistent Live session
per headset connection replaces per-turn HTTP + TTS calls.

## 2. Session ownership

The coordinator owns exactly one `LiveSession` per headset connection,
warmed at `hello` (measured setup 0.28 s) so the first question skips
dial-up. Idle sessions cost nothing; on socket drop or
`clear_session` the session closes and all media clears. Reconnect
warms a fresh session. `voice-stub --perception-qa` keeps its offline
twin (no session, test tone).

## 3. Media flow

- Mic: Quest 16 kHz chunks stream as today and the coordinator
  forwards them immediately as Live `realtimeInput` audio (16 kHz PCM —
  no input resample). The model hears speech incrementally.
- Image: on `utterance_end`, after validation, the coordinator sends
  one `clientContent` turn carrying the JPEG plus a short text
  ("answer what I just asked about this image, at most twenty-five
  words"). The speech is already in context; it is never re-sent.
- Voice: model audio (24 kHz PCM) streams out, is resampled to the
  16 kHz Quest contract via laptop `ffmpeg` as today, and is forwarded
  as ordered `speak_chunk {turn_id, seq, data_b64}` frames plus one
  terminal `speak_final {text, voice_gate}`. Quest plays chunks as they
  arrive; first audio lands ~1–2 s after silence.
- Transcripts: `inputAudioTranscription` is the `heard` text (context +
  caption, never a reply); `outputTranscription` is the `said` text.
  Neither is logged beyond the caption path.
- Missing/rejected images keep the fixed local recovery reply with no
  model call. Short/offline turns make no call. Replies stay
  twenty-five words or fewer by instruction plus server-side sentence
  truncation before streaming starts.

## 4. Interruption

The VAD stays open during playback with a dedicated barge-in threshold
(`0.05` RMS initial constant, device-tuned against speaker leakage —
measure mic RMS during speaker playback vs live voice on device; if the
speaker trips it, raise the constant rather than adding filtering):
onset stops local playback immediately and the streamed audio
continues to the session; the server `INTERRUPTED` event is ground
truth and makes the coordinator emit `stop_speak` (idempotent —
already-stopped playback stays stopped) and tombstone the turn. Late
model audio for tombstoned turns is dropped, never played. No
controller binding exists anywhere in this path.

## 5. Usage and audit

Each model turn records one ledger entry (`rt-voice-turn`) from that
turn's `usageMetadata`, with modality details where reported. Session
usage fields are cumulative, so the adapter records per-turn deltas
and retains a missing-usage indicator when an event carries none. No
prompts, media, transcripts, replies, or key material enter the
ledger. Failures append failure records.

## 6. Errors

Session connect failure, mid-turn socket loss, image rejection, and
empty replies all produce the existing honest recovery lines and
visible recovery hints, never invented vision and never a silent stall.
A turn with no reply within 45 s fails visibly and frees the mic gate.
Blips (under three voiced 100 ms windows) and speaker echo (envelope
correlation against the just-played reply) never become turns: the
utterance drops with `utterance_dropped` plus the echo score, and a
silent `stop_speak` releases the Quest mic gate with no caption and no
model call. One fixed Live voice (`Kore`) so the speaker never changes
mid-conversation.
Logs carry enums, counts, dimensions, durations, and exception classes
only.

## 7. Out of scope

Word-by-word live captions, TTS voice selection, server-side echo
cancellation, multi-device sessions, and the Qwen realtime route. The
HTTP perception planner remains as the audited fallback path and for
tests, not the live loop.
