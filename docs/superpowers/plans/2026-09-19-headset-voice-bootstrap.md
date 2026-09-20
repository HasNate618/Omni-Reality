# Headset Voice Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Quest wearer speak without a controller and hear an audio-only Omni response through the headset before spatial capture is reintroduced.

**Architecture:** A pure `VoiceActivityGate` owns pre-roll, onset, silence, and maximum-duration decisions. `MicUtterance` owns platform microphone capture and uses that gate to open/close a bounded websocket utterance. The coordinator gains an offline tone-backed `voice-stub` and an audio-only Yibu mode that bypasses spatial tools and operations; `SpeakCloudPlayer` remains the only playback path.

**Tech Stack:** Unity 6 / C# / NUnit EditMode; Python 3 unittest; WebSocket coordinator; Yibu HTTP Omni and Gemini Live cloud PCM only in explicitly invoked live mode.

## Global Constraints

- No camera, JPEG, envelope, gaze, ray, depth, target, scene operation, marker, label, ghost, or procedural operation belongs in the bootstrap voice path.
- `YIBU_API_KEY` remains only in the laptop environment; Unity, the repo, protocol payloads, and logs never contain it.
- Quest streams only 16 kHz mono `pcm_s16le`; VAD uses 100 ms chunks and RMS threshold `0.01`.
- Local pre-roll is `300 ms`, silence closure is `800 ms`, and maximum utterance duration is `8 s`; coordinator retains its `0.5 s` minimum.
- `voice-stub` returns a documented non-speech test tone and spends no credit; only `--planner yibu --voice-only` calls cloud services.
- Live call purposes are short labels; audit data remains redacted and missing token counts remain `null`.
- All new behavior starts test-first. Do not modify vendored Unity package-cache files.
- Preserve the existing spatial modes and their binding specs; this is a separate audio-only bootstrap path.

---

### Task 1: Make voice activation a pure, testable VAD state machine

**Files:**
- Create: `QuestDemo/Assets/Voice/VoiceActivityGate.cs`
- Create: `QuestDemo/Assets/Voice/VoiceActivityGate.cs.meta`
- Modify: `QuestDemo/Assets/Tests/Editor/MicUplinkTests.cs`
- Modify: `QuestDemo/Assets/Tests/Editor/MicUplinkTests.cs.meta` only if Unity regenerates it

**Interfaces:**
- Produces `VoiceActivityGate` with `Observe(List<short> chunk)` returning `Idle`, `Started`, `Streaming`, or `Ended`.
- `Started` exposes exactly the retained pre-roll chunks followed by the onset chunk; `Ended` occurs after eight consecutive 100 ms below-threshold chunks or eighty utterance chunks total.
- Later Task 2 calls `Observe` for every captured 1,600-sample chunk and sends exactly the chunks returned by the gate.

- [ ] **Step 1: Write the failing EditMode tests**

Add tests that construct 1,600-sample chunks of amplitude `0`, `400`, and `1000` and assert:

```csharp
[Test]
public void SpeechOnsetReturnsThreeChunkPrerollThenOnset()
{
    var gate = new VoiceActivityGate();
    gate.Observe(Chunk(0));
    gate.Observe(Chunk(0));
    gate.Observe(Chunk(0));
    VoiceActivityDecision start = gate.Observe(Chunk(1000));
    Assert.AreEqual(VoiceActivityState.Started, start.State);
    Assert.AreEqual(4, start.Chunks.Count);
}

[Test]
public void EightSilentChunksEndAnOpenUtterance()
{
    var gate = StartedGate();
    for (int i = 0; i < 7; i++) Assert.AreEqual(VoiceActivityState.Streaming, gate.Observe(Chunk(0)).State);
    Assert.AreEqual(VoiceActivityState.Ended, gate.Observe(Chunk(0)).State);
}

[Test]
public void EightyChunksEndAnOpenUtteranceEvenWhenSpeechContinues()
{
    var gate = StartedGate();
    VoiceActivityDecision decision = default;
    for (int i = 0; i < 79; i++) decision = gate.Observe(Chunk(1000));
    Assert.AreEqual(VoiceActivityState.Ended, decision.State);
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd QuestDemo && <Unity batchmode EditMode test command for MicUplinkTests>
```

Expected: compile failure because `VoiceActivityGate` does not exist. If host Unity test execution remains blocked, record that limitation and run a C#-independent compilation check only; do not claim EditMode success.

- [ ] **Step 3: Implement the minimal pure gate**

Create `VoiceActivityGate` with constants `ChunkSamples = 1600`, `SilenceRms = 0.01f`, `PreRollChunks = 3`, `SilenceChunksToEnd = 8`, and `MaxUtteranceChunks = 80`. Compute RMS from signed 16-bit samples. Keep a three-item ring buffer only while idle. On onset return the retained chunks plus onset and reset the silence counter. While open, return the passed chunk. On either terminal threshold, return `Ended` with the final chunk and reset to idle.

- [ ] **Step 4: Run the focused tests again**

Run the same EditMode command. Expected: all `MicUplinkTests` pass, subject to the documented host Unity limitation.

- [ ] **Step 5: Commit the task**

```bash
git add QuestDemo/Assets/Voice/VoiceActivityGate.cs QuestDemo/Assets/Voice/VoiceActivityGate.cs.meta QuestDemo/Assets/Tests/Editor/MicUplinkTests.cs
git commit -m "Add bounded voice activity gate"
```

### Task 2: Wire microphone VAD to a controller-free websocket utterance

**Files:**
- Modify: `QuestDemo/Assets/Spatial/MicUtterance.cs`
- Modify: `QuestDemo/Assets/Spatial/SpatialRuntime.cs`
- Modify: `QuestDemo/Assets/Voice/SpeakCloudPlayer.cs`
- Modify: `QuestDemo/Assets/Plugins/Android/AndroidManifest.xml`
- Modify: `QuestDemo/Assets/Tests/Editor/MicUplinkTests.cs`

**Interfaces:**
- Consumes Task 1 `VoiceActivityGate` decisions.
- `MicUtterance` requests Android `RECORD_AUDIO` once and auto-starts local capture after it is granted.
- `SpeakCloudPlayer.IsPlaying` is true only during active PCM playback.
- `SpatialRuntime` creates/ticks `CoordinatorClient`, `SpeakCloudPlayer`, and `MicUtterance` without requiring `PassthroughCameraAccess` to be playing.

- [ ] **Step 1: Write failing tests for the Unity seams**

Add reflection-safe EditMode tests asserting:

```csharp
[Test]
public void MicUplinkUsesVoiceActivityGateThresholdAndDurations()
{
    Assert.AreEqual(0.01f, MicUtterance.SilenceRms);
    Assert.AreEqual(16000, MicUtterance.SampleRate);
    Assert.AreEqual(1600, MicUtterance.ChunkSamples);
    Assert.AreEqual(800, MicUtterance.SilenceEndMs);
    Assert.AreEqual(8000, MicUtterance.MaxUtteranceMs);
}

[Test]
public void PlayerReportsPlaybackState()
{
    Assert.IsNotNull(typeof(SpeakCloudPlayer).GetProperty("IsPlaying"));
}
```

- [ ] **Step 2: Run the focused test and observe the expected failure**

Run the Task 1 EditMode command. Expected: missing `SilenceEndMs`, `MaxUtteranceMs`, and `IsPlaying` members.

- [ ] **Step 3: Implement the controller-free integration**

- Add `android.permission.RECORD_AUDIO` to the manifest.
- In `MicUtterance`, request `Application.RequestUserAuthorization(UserAuthorization.Microphone)` on Android before calling `Microphone.Start`; remain locally idle when not authorized.
- Start a looping microphone clip once authorized, pump 1,600-sample chunks, use `VoiceActivityGate`, and create/send an utterance only when it returns `Started`. Send retained + onset chunks exactly once. On `Ended`, enqueue `utterance_end`, clear the utterance ID, and retain no old chunks.
- Before opening an utterance, require an open `CoordinatorClient` and `!SpeakCloudPlayer.IsPlaying`; do not record a second turn during response playback.
- Retain `BeginUtterance`, `EndUtterance`, and `SimulateKeyword` only as debug/test fallbacks; automatic VAD is the default production path.
- Add read-only `SpeakCloudPlayer.IsPlaying` backed by its `AudioSource` state.
- Remove the `PassthroughCameraAccess.IsPlaying` guard from `SpatialRuntime.TickCoordinator`; when an IP is configured, create the coordinator object and attach `SpeakCloudPlayer` and `MicUtterance` to it. Do not enqueue a frame from the voice path.
- Hide the aim line unless a right Touch controller is actually connected. Preserve its controller debug behavior.

- [ ] **Step 4: Run focused tests and static diagnostics**

Run the EditMode command and:

Use `lsp_diagnostics` for `MicUtterance.cs`, `SpatialRuntime.cs`, and `SpeakCloudPlayer.cs`.

Expected: focused assertions pass when Unity is available; no C# diagnostic errors. Record the host-libxml limitation if it still prevents execution.

- [ ] **Step 5: Commit the task**

```bash
git add QuestDemo/Assets/Spatial/MicUtterance.cs QuestDemo/Assets/Spatial/CoordinatorClient.cs QuestDemo/Assets/Spatial/SpatialRuntime.cs QuestDemo/Assets/Voice/SpeakCloudPlayer.cs QuestDemo/Assets/Plugins/Android/AndroidManifest.xml QuestDemo/Assets/Tests/Editor/MicUplinkTests.cs
git commit -m "Add controller-free microphone voice turns"
```

### Task 3: Add offline voice-stub PCM and audio-only live planning

**Files:**
- Modify: `provider/coordinator/server.py`
- Modify: `provider/coordinator/planner.py`
- Modify: `provider/coordinator/turn.py`
- Create: `provider/voice/test_tone.py`
- Create: `provider/tests/test_voice_bootstrap.py`
- Modify: `provider/tests/test_voice_turn.py`

**Interfaces:**
- Adds CLI planner `voice-stub`; it returns `PlanResult(ops=[])`, caption text, and a deterministic non-speech 16 kHz PCM tone.
- Adds `--voice-only` valid only with `--planner yibu`; it passes PCM without JPEG and produces zero scene operations.
- `voice_only` planner turns bypass `bind_tools`, `run_tool_loop`, and spatial prompt instructions.

- [ ] **Step 1: Write failing provider tests**

Create tests with a fake send function and a 0.6-second PCM buffer that assert:

```python
def test_voice_stub_sends_nonempty_pcm_and_no_scene_ops():
    state = CoordinatorState(planner=make_planner("voice-stub"))
    # run turn with a fake sender
    assert [kind for kind, _payload in sent] == ["turn_started", "speak"]
    assert speak["audio"]["encoding"] == "pcm_s16le"
    assert speak["audio"]["sample_rate"] == 16000
    assert base64.b64decode(speak["audio"]["data_b64"])


def test_voice_only_yibu_uses_audio_without_tools_or_ops():
    planner = YibuPlanner(voice_only=True, complete_fn=fake_text_completion)
    plan = asyncio.run(planner.plan(pcm=PCM, jpeg=None, envelope=None, context=[]))
    assert plan.ops == []
    assert completion_tool_flags == [False]
```

Also assert `make_planner("voice-stub")` has no spatial operations and command-line validation rejects `--voice-only` with `mark`, `stub`, or `voice-stub`.

- [ ] **Step 2: Run the provider tests and observe the expected failure**

Run:

```bash
cd provider && . .venv/bin/activate && python -m unittest tests.test_voice_bootstrap -v
```

Expected: import/argument failures because the mode and tone helper do not exist.

- [ ] **Step 3: Implement the smallest server/planner changes**

- Implement `provider/voice/test_tone.py` with `make_test_tone(sample_rate=16000, duration_ms=250) -> bytes`; generate a deterministic, bounded, signed-16-bit mono tone with no provider dependency.
- Extend `make_planner` and the CLI choices with `voice-stub`. In `run_server`, attach the test-tone synthesizer only for that planner.
- Add `voice_only: bool = False` to `YibuPlanner`; use a dedicated audio-only system prompt that requests a concise spoken answer and no JSON/spatial operations.
- In `YibuPlanner.plan`, when `voice_only`, call its injectable completion function once with tools disabled, parse text/heard, and return `PlanResult(ops=[], ...)` without calling `bind_tools`, worker tools, or spatial-op validation.
- Add `--voice-only`; fail parser validation unless planner is `yibu`. When enabled, set `YibuPlanner(voice_only=True, purpose="voice-only-turn")`, and label cloud synthesis `voice-only-speak`.
- In `_run_turn`, skip `bind_tools` for a voice-only planner. Its empty op list goes directly to the existing synthesizer, preserving `turn_started` then `speak` order.

- [ ] **Step 4: Run focused and full offline provider tests**

Run:

```bash
cd provider && . .venv/bin/activate && python -m unittest tests.test_voice_bootstrap tests.test_voice_turn -v
python -m unittest discover -s tests -v
```

Expected: both commands exit 0. Do not call Yibu or Gemini during these tests.

- [ ] **Step 5: Commit the task**

```bash
git add provider/coordinator/server.py provider/coordinator/planner.py provider/coordinator/turn.py provider/voice/test_tone.py provider/tests/test_voice_bootstrap.py provider/tests/test_voice_turn.py
git commit -m "Add audio-only headset voice planner modes"
```

### Task 4: Remove bootstrap-only visuals and produce a clean headset build

**Files:**
- Modify: `QuestDemo/Assets/Editor/BuildAndroid.cs`
- Modify: `docs/omni-spatial-loop.md`
- Modify: `docs/omni-worker-tools.md` only if its run commands mention the old controller voice proof

**Interfaces:**
- A build removes the named `DemoCube` object before saving its in-memory build scene; it does not commit Unity's unrelated scene serialization churn.
- No-controller builds render no aim line.
- Documentation distinguishes `voice-stub` transport proof, live audio-only smoke, and later spatial voice turns.

- [ ] **Step 1: Write a failing edit-time test**

Add an Editor test that creates a `DemoCube`, invokes the extracted `BuildAndroid.RemoveBootstrapDemoObjects(Scene)` helper, and asserts `GameObject.Find("DemoCube")` is null. Add a test that `SpatialRuntime` does not enable its aim line when `OVRInput.IsControllerConnected(OVRInput.Controller.RTouch)` is false through a test seam/helper.

- [ ] **Step 2: Run the focused tests and observe expected failures**

Run the Quest EditMode test command. Expected: missing cleanup/helper methods.

- [ ] **Step 3: Implement the minimal cleanup and docs**

- Extract and call `BuildAndroid.RemoveBootstrapDemoObjects(Scene)` before every build; it destroys only the named `DemoCube` and marks the scene dirty.
- Keep `BuildAndroid.Build` release behavior unchanged and retain `BuildDevelopment` only for device diagnostics.
- Update `docs/omni-spatial-loop.md` with exact offline and live commands:

```bash
python -m coordinator.server --planner voice-stub
python -m coordinator.server --planner yibu --voice-only
```

Document that the first produces a test tone and the second spends approved credit for live conversation.

- [ ] **Step 4: Verify build and deploy artifact**

Run the licensing-wrapped FHS Unity build from `docs/questdemo-build.md` using `BuildDevelopment`; verify `BUILD SUCCEEDED: Builds/QuestDemo.apk`. Install it only after the build succeeds. Do not claim a headset conversation pass until Task 5 evidence exists.

- [ ] **Step 5: Commit the task**

```bash
git add QuestDemo/Assets/Editor/BuildAndroid.cs QuestDemo/Assets/Tests/Editor docs/omni-spatial-loop.md docs/omni-worker-tools.md
git commit -m "Prepare Quest for voice-only bootstrap"
```

### Task 5: Perform bounded device proofs and the approved live smoke

**Files:**
- Modify: `docs/omni-spatial-loop.md` only if observed commands or failure handling differ from Task 4.
- Do not commit media, PCM, logs, API keys, tokens, or artifacts.

**Interfaces:**
- Requires Task 4 development APK on Quest and `laptop_ipv4` set to the Tailscale server IP through the existing debuggable test-build path.
- Requires laptop coordinator at port 8765 and headset-to-server Tailscale reachability already demonstrated.

- [ ] **Step 1: Run the offline headset transport proof**

Start:

```bash
cd provider && . .venv/bin/activate && python -m coordinator.server --planner voice-stub
```

Wear the headset, grant microphone permission, speak for at least one second, then remain silent. Capture only redacted evidence: coordinator saw `turn_started`/audio and Quest logged/played the test tone caption. If this fails, stop and create a regression test before changing code.

- [ ] **Step 2: Run the approved minimal live smoke**

With `YIBU_API_KEY` in the laptop environment only, start:

```bash
cd provider && . .venv/bin/activate && python -m coordinator.server --planner yibu --voice-only
```

Speak one short non-sensitive sentence. Verify a new audit ledger row has a short purpose and that `usage_raw`, `error`, and `source.path` contain no media, prompt, reply, or key. Verify Quest plays nonempty cloud PCM and its matching caption.

- [ ] **Step 3: Record only non-sensitive verification result**

Update the runbook with pass/fail status and exact non-secret commands if needed. Never add the utterance, response text, audio, request body, audit row body, IP-auth tokens, or key material.

- [ ] **Step 4: Run final verification commands**

```bash
cd provider && . .venv/bin/activate && python -m unittest discover -s tests -v
cd .. && git status --short
```

Expected: provider suite exits 0; the only remaining dirty files are explicitly inspected Unity-generated changes, never accidental media or secrets.

- [ ] **Step 5: Commit only documentation changes, if any**

```bash
git add docs/omni-spatial-loop.md docs/omni-worker-tools.md
git commit -m "Document headset voice bootstrap verification"
```
