# Perception Voice-Q&A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. User requested inline execution, without delegates. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer a hands-free spoken question about the current camera view, with one JPEG, no scene operations, recoverable failures, and no idle cloud calls.

**Architecture:** Negotiate `perception_qa` in hello_ok; existing audio-only modes keep sending no image. Mic audio streams immediately; when speech closes, an asynchronous capture freezes image metadata and queues the JPEG before the final utterance_end in one FIFO media lane. A separate tools-disabled planner uses one audio+image HTTP call and the existing speech adapter.

**Tech Stack:** Unity 6 / Meta PCA / AsyncGPUReadback, Python asyncio, existing yibu HTTP and Gemini speech helpers.

## Global Constraints

- Worktree `/home/nate/Projects/omni-slices-0-2`, branch `spatial-model-unity-integration`; preserve existing dirty files and speech-format patch.
- No live provider calls, no headset install/launch during implementation. Tests inject only the network boundary. Build an APK only after offline tests.
- 16 kHz mono s16le; 100 ms PCM chunks; 0.5 s minimum; retain existing VAD thresholds and 8 s close.
- JPEG <= 65,536 bytes; preserve aspect ratio, longest side <= 640; full 1280x960 input normally becomes 640x480, not a distorted 612x408.
- JPEG quality 60, then one local quality-35 retry under byte cap. No provider retry.
- Wait at most 750 ms for capture/readback; only snapshot a PCA update <=500 ms old. Capture-time original dimensions/intrinsics/pose remain original; sent dimensions describe JPEG; identity normalized crop.
- Offline/short turns drop with local feedback and no cloud call. Missing camera on an otherwise valid connected turn yields explicit visual-unavailable context, never invented vision.
- Logs: enums, counts, dimensions, durations, exception classes. No media, captions, prompts, replies, keys or raw validation errors.
- Tools/ops disabled; at most one Omni + one TTS call per accepted valid turn. Purpose labels `perception-qa-turn` / `perception-qa-speak` (including degraded camera turns; never mislabeled voice-only).

---

### Task 1: Tools-free perception planner and validated image boundary

**Files:** Create `provider/coordinator/perception.py`, `provider/voice/perception_image.py`, `provider/tests/test_perception.py`. Modify `provider/coordinator/{server,session,turn,planner}.py`, `provider/voice/bootstrap_diagnostics.py`.

**Interfaces:** `PerceptionQaPlanner(complete_fn=None, model=..., max_tokens=128).plan(pcm,jpeg,envelope,context) -> PlanResult`; `validate_jpeg(jpeg, envelope) -> str | None` (closed rejection reason or success). `make_planner(..., perception_qa=False)`; `run_server(..., perception_qa=False)`; CLI `--perception-qa` permits yibu or voice-stub only, excludes voice-only. hello_ok boolean `perception_qa` defaults false on old peers.

- [ ] Write failing offline tests for real planner request, JPEG/WAV content, tools false, zero ops, no false transcript, no previous image reuse, malformed/oversize/mismatched image, missing-key prebind and CLI mode conflicts.

```python
async def complete(messages, tools_enabled):
    assert tools_enabled is False
    assert [p['type'] for p in messages[-1]['content']] == ['text', 'image_url', 'input_audio']
    return {'choices': [{'message': {'content': 'A red test square.'}}]}
# await planner.plan(...) must return ops=[] and heard=None.
```

- [ ] Run `.venv/bin/python -m unittest tests.test_perception -v` and confirm missing feature failure.
- [ ] Implement separate planner; never bind spatial tools. Use strict base64 length cap and bounded JPEG marker/dimension validation before forwarding. Structural validation is not a complete image decoder; provider errors remain explicit. Reject duplicate/late frame/end and fence media after close. Cancel tasks and clear media/context on connection loss and clear_session. Suppress schema exception payload logging.
- [ ] Run focused and whole provider suites. Fake socket turn test must produce only turn_started/speak and exactly one completion/synthesis.
- [ ] Update provider and spatial topic docs in Task 4; no live call.

### Task 2: Ordered media transport and recoverable turn state

**Files:** Modify `QuestDemo/Assets/Spatial/{CoordinatorClient,ProtocolJson,MicUtterance}.cs`. Create `QuestDemo/Assets/Tests/Editor/PerceptionTransportTests.cs`.

**Interfaces:** `ProtocolJson.BuildFrame(session,env,utteranceId,jpegBase64)`; negotiated `CoordinatorClient.PerceptionEnabled`; `EnqueuePerceptionFrame(utteranceId,env,jpeg)` followed by `EnqueueUtteranceEnd`; FIFO media priority shared by audio/frame/end, cancel/ack remain ahead. Expose pending-reply state to mic gate; no next question until reply or 45-second failure feedback.

- [ ] Add failing tests for queue ordering using real SendQueue, JPEG serialization, default-off negotiation, offline drop, and cleanup discarding queued media.

```csharp
queue.Enqueue(CoordinatorClient.PriorityAudioChunk, "audio");
queue.Enqueue(CoordinatorClient.PriorityFrame, "frame");
queue.Enqueue(CoordinatorClient.PriorityUtteranceEnd, "end");
queue.Enqueue(CoordinatorClient.PriorityAck, "ack");
// Dequeue sequence must be ack, audio, frame, end.
```

- [ ] Run licensing-wrapped Unity EditMode tests; confirm order failure.
- [ ] Implement FIFO priorities, session fencing and stale queue cleanup including fast-close-before-first-Tick reconnect. Mic drops active turn when connection/session changes and resets local pre-roll/pending samples. Reply errors and speech captions use visible headset feedback, never logs.
- [ ] Re-run Unity tests; use a loopback websocket integration test where practical for actual serialization/order.

### Task 3: Single PCA readback and capture-time geometry

**Files:** Create `QuestDemo/Assets/Spatial/{PerceptionCapture,PerceptionImage}.cs`, `QuestDemo/Assets/Tests/Editor/PerceptionCaptureTests.cs`. Modify `SpatialRuntime.cs` and `MicUtterance.cs`.

**Interfaces:** `PerceptionCapture.Capture(Action<CaptureEnvelope,byte[],string>)`; `Cancel()` fences late readbacks. `PerceptionImage.FitSize(w,h)` and CPU downsample/encode utility keep orientation/aspect. `SpatialRuntime.TryCapture(..., requireDepth=false, includePointing=false)` allows honest depth misses and records cached capture geometry. Runtime wires one capture component to the mic; no scene edits needed.

- [ ] Test aspect preservation with literal 1280x960 -> 640x480, portrait ->480x640, no upscaling; image-corner orientation; JPEG byte cap; timeout/cancel/late result and no duplicate end.

```csharp
Assert.AreEqual(new Vector2Int(640, 480), PerceptionImage.FitSize(1280, 960));
Assert.AreEqual(new Vector2Int(320, 240), PerceptionImage.FitSize(320, 240));
```

- [ ] Run red Unity tests.
- [ ] Capture request is scheduled in LateUpdate after PCA Update. Require a fresh SDK frame; freeze metadata before AsyncGPUReadback.Request. Callback owns only that frame, never re-queries current head pose; discard if timed out/cancelled/disconnected/stage changed. CPU resize preserves source orientation and aspect; no blocking Graphics.Blit (SDK warns that returns previous texture). Always release temporary Texture2D resources. Limit one outstanding native request until its callback completes.
- [ ] On completion or capture failure close the same utterance; image success queues frame before end, camera failure closes audio-only with visible recovery hint. Offline drops both. Verify all Unity tests.

### Task 4: Documentation, self-review and offline build

**Files:** Update `docs/superpowers/specs/2026-09-19-perception-voice-qa-design.md`, `docs/omni-provider-api.md`, `docs/omni-spatial-loop.md`, `docs/questdemo-build.md`, `provider/README.md`.

- [ ] Correct draft contradictions: no audio resend, no offline cloud fallback, short turns drop, local pre-roll is not cloud recording, aspect-preserving resize, negotiated mode, explicit error recovery, bounded calls rather than guaranteed successful calls.
- [ ] Record future extension seam accurately: pixel targets need calibrated UV -> capture ray -> depth/scene hit. SAM2 masks alone do not give metric depth or current-view registration; real-time masks require tracking/reprojection. Frame IDs preserve provenance, not a promise that no future protocol fields are needed.
- [ ] Run LSP diagnostics before build, full provider tests and Unity EditMode suite, `git diff --check`, and lens session diagnostics. Inspect only owned diffs.
- [ ] Build `BuildAndroid.BuildDevelopment` using licensing-wrapped FHS command from `docs/questdemo-build.md`. No install/launch or live model call.
- [ ] Report actual results and remaining physical acceptance: camera permission/runtime, orientation/corner calibration, fresh object Q&A, covered lens honesty and recovery. Never claim headset pass from unit tests or a successful build.
