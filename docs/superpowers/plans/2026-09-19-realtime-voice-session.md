# Realtime Voice Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Inline execution chosen. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-turn HTTP+TTS stitch with one persistent Live session per headset connection: ~1–2 s first audio, voice barge-in, no controller.

**Architecture:** New `provider/voice/live_session.py` owns the WS (asyncio); coordinator holds one per connection, forwards mic chunks live, sends the image turn on utterance_end, streams 24→16 kHz speak chunks; Quest plays chunks incrementally and keeps VAD open during playback with a barge-in threshold.

**Tech Stack:** Python asyncio + websockets client, existing ffmpeg resample, Unity AudioClip streaming append.

## Global Constraints

- Worktree `/home/nate/Projects/omni-slices-0-2`, branch `spatial-model-unity-integration`; never stage Unity dirt, speech-fix files, or shared topic docs with foreign hunks.
- No live provider calls during implementation. All session I/O tests use an in-process fake WS. No headset install/launch.
- 16 kHz mono s16le Quest contract; Live audio in 16 kHz (no resample), out 24 kHz → ffmpeg → 16 kHz.
- Replies ≤ 25 words by instruction + server-side sentence truncation.
- Redacted logs/diagnostics only; audit `rt-voice-turn` per model turn from usage deltas; failures logged.
- Barge-in threshold constant `0.05` RMS initial; device-tunes later.

---

### Task 1: LiveSession with fake-WS tests

**Files:** Create `provider/voice/live_session.py`, `provider/tests/test_live_session.py`.

**Interfaces:** `LiveSession(api_key, model, endpoint, callbacks)`. `await connect()` (setup handshake, raises + audits on failure). `await send_audio(pcm: bytes)` (realtimeInput, drops when closed). `await start_image_turn(jpeg: bytes, text: str)` (clientContent, truncates text to 2 sentences). Callbacks: `on_audio(bytes24k)`, `on_input_transcript(str)`, `on_output_transcript(str)`, `on_interrupted()`, `on_usage(dict)`, `on_error(str)`. `await close()`.

- [ ] Write failing tests with a fake WS object recording sends and replaying canned server events: setup handshake, audio chunk framing (base64, mime audio/pcm rate 16000), image turn shape, audio-out dispatch, interrupted dispatch, usage passthrough, send-after-close drop, setup-reject raises.

```python
async def test_image_turn_shape(self):
    ws, session = make_session()
    await session.start_image_turn(b'\xff\xd8fake', 'Hi.')
    frame = json.loads(ws.sent[-1])
    self.assertEqual(frame['clientContent']['turnComplete'], True)
```

- [ ] Run `.venv/bin/python -m unittest tests.test_live_session -v`, confirm missing-feature failure.
- [ ] Implement asyncio session: sequential send lock, background receive loop dispatching audio/transcript/interrupted/usage, never raises out of callbacks (logs exception class), close cancels loop.
- [ ] Sentence truncation: split reply on `.!?`, keep first 2 non-empty, used for the image-turn instruction text only (model output truncation happens in coordinator before streaming? No — output streams; truncation applies to the instruction prompt. Model-side brevity via instruction "at most twenty-five words").
- [ ] Run focused + full provider suites.

### Task 2: Coordinator session ownership + chunked speak

**Files:** Modify `provider/coordinator/{server,session,turn}.py`, `provider/voice/cloud_speech.py` (resample helper reuse). Extend `provider/tests/test_perception.py` (new test class, no live calls).

**Interfaces:** `CoordinatorState.live: LiveSession | None`. Hello warms session (fail → voice-stub-equivalent honest line, no crash). `ingest_audio_chunk` also forwards to live. `utterance_end` → validate image → `start_image_turn` (missing → fixed recovery, no call). Audio-out → resample each chunk → `speak_chunk {turn_id, seq, data_b64}`; turn end → `speak_final {text, voice_gate}`. `on_interrupted` → `stop_speak` + tombstone. Usage deltas → `rt-voice-turn` ledger entries.

- [ ] Failing tests with injected fake LiveSession: chunk forwarding order, image-turn on end only, recovery on missing image with zero session calls, interrupted → stop_speak + tombstone + late audio dropped, usage delta math (cumulative 169→213 records 44, not 213), session-connect failure degrades honestly.
- [ ] Run red, implement, run green + full suite.
- [ ] Keep HTTP perception planner untouched as fallback; `--perception-qa` selects session path, failure to connect falls back to honest line (not HTTP).

### Task 3: Quest streaming playback + voice barge-in

**Files:** Modify `QuestDemo/Assets/Voice/SpeakCloudPlayer.cs`, `QuestDemo/Assets/Spatial/{CoordinatorClient,MicUtterance}.cs`, `QuestDemo/Assets/Spatial/ProtocolJson.cs`. Create `QuestDemo/Assets/Tests/Editor/RealtimeVoiceTests.cs`.

**Interfaces:** `ProtocolJson.TryParseSpeakChunk/TryParseSpeakFinal`; `SpeakCloudPlayer.AppendChunk(turnId, pcm)` (30 s ring clip, plays while writing) + `FinishTurn(turnId)`; `MicUtterance.BargeInRms = 0.05f` gate during playback (onset → local StopPlayback, keep streaming); `stop_speak` → stop (idempotent).

- [ ] Failing tests: chunk append order + clip growth, wrong-turn chunk ignored, finish without chunks degrades, barge-in threshold blocks normal playback-level RMS but passes loud onset, stop_speak idempotent, late chunk after tombstone dropped.
- [ ] Run red via licensing-wrapped EditMode, implement, run green + full Unity suite.
- [ ] No controller references added; PTT paths untouched.

### Task 4: Docs, verification, offline build

**Files:** Update realtime spec (tuning notes), `docs/omni-spatial-loop.md` (session loop, chunk protocol), `docs/omni-provider-api.md` (rt-voice-turn row, IMAGE token note).

- [ ] Correct contradictions in same commit as code where separable; shared docs with foreign hunks stay uncommitted.
- [ ] LSP diagnostics clean, full provider + Unity suites green, `git diff --check` on owned files.
- [ ] Licensing-wrapped `BuildAndroid.BuildDevelopment`. No install/launch/live.
- [ ] Report evidence + remaining live acceptance (first-audio latency, interruption device test, echo threshold tuning).
