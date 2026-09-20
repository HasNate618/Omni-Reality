# SAM 2 Streaming Architecture

## Selection latency, measured

Numbers from `provider/tools/bench_tracking.py` (21 gateway calls) and a
direct probe of the SAM 2 socket, on the Mac + Quest 3S setup.

| stage | cost | notes |
| --- | --- | --- |
| model call (`qwen3.8-omni-flash`) | **2.3-2.7 s**, outliers to 5.3 s | dominates; see below |
| SAM 2 connect | 15 ms | per tracking session |
| SAM 2 first inference | 88-125 ms | no per-connection penalty; `warm_up()` covers it |
| SAM 2 steady state | ~105 ms/frame | |
| catch-up replay | model latency x `streamFps` x 105 ms | ~1-2 s at `streamFps: 3` |
| spoken reply | 2.3 s cold, **0 ms cached** | canned lines warmed at startup |

**Levers that do nothing.** Each was measured, not assumed:

- Image size 640 -> 320 px (27 KB -> 10 KB): no change.
- Dropping `heard` from the tracking reply: no change.
- `max_tokens` 96 -> 32: no change.
- Utterance 2.35 s -> 1.02 s: no change.
- Reusing one `httpx` connection instead of one per call: no change (a
  1-token request still costs ~1 s, so that is gateway floor, not handshake).

So the model call is a fixed ~2.5 s of gateway time that payload tuning does
not touch. What remains controllable is everything around it.

**What was changed.** The spoken line is cached (2.3 s -> 0), and the seed
mask is now published before the backlog is replayed, so it appears as soon
as the point lands instead of one catch-up later.

**Still available, with trade-offs:**

- Lower `streamFps` to 2 in `QuestTrackingSettings`: shrinks the backlog by a
  third, at coarser tracking of fast motion.
- Subsample the catch-up (replay every 2nd frame): roughly halves it; SAM 2
  tolerates gaps but is likelier to lose a fast-moving object.
- Skip catch-up entirely and jump from the seed frame to the newest: removes
  it, and is the most likely to lose the object outright.

## What it is
A decoupled client-server architecture for real-time SAM 2 tracking. 
- **Server** (`sam2/sam2_ws_server.py`): Hosts the SAM 2 PyTorch model locally (CUDA, Apple Silicon MPS, or CPU). It asynchronously accepts base64-encoded frames and click coordinates over WebSockets, and returns base64-encoded segmentation masks.
- **Client** (`sam2/sam2_ws_client.py`): A Python-based proof-of-concept for the eventual Quest 3S Unity app. It captures webcam video at 30+ FPS, sends the newest frame (downscaled to at most 1024 px on the long side) as soon as the previous result arrives, and overlays the returned masks without blocking the main rendering loop. Masks are decoded once per result in the network thread, not per displayed frame.

## Contract / Rules
- **Dependencies**: The architecture requires `websockets>=11.0` and `opencv-python>=4.8.0`. Install them via `pip install -r sam2/requirements-ws.txt`.
- **Payload Format**: 
  - Client sends `{"type": "frame", "jpeg_b64": <string>, "clicks": [{"x": int, "y": int, "obj_id": int}]}`. Click coordinates are in the pixels of the sent (downscaled) frame.
  - Server replies `{"type": "result", "objects": [{"obj_id": int, "mask_b64": <string>}]}`; masks are PNGs at the first frame's resolution. Keep dimensions fixed within a connection.
  - Several objects are clicked by sending several `clicks` entries with distinct `obj_id`s. Doing that on **one** frame is what the Quest path uses: each is an initial conditioning frame, so the reply carries a mask for every object.
  - An optional string `frame_id` on a request is echoed in its result. Existing webcam requests without it still work.
  - Object removal (`removes`, right-click) is **not implemented** on either side yet.
- **Memory Safety**: The server keeps a rolling window. It drops raw image tensors older than 5 frames and keeps only a small set of past frames per object in tracking memory (`SAM2_MEMORY`, below). Clicked (conditioning) frames are kept, and attention uses at most the 2 closest per object. Device memory stays flat over long sessions.
- **Backpressure**: One frame in flight. The client waits for each result before sending the newest frame, so frames never queue up and there is no fixed sleep.
- **Multi-Threading**: The client uses two threads: the main UI thread captures the camera and renders; a background network thread encodes, sends, and decodes results.
- **Motion compensation (client)**: a mask arrives one round-trip after its frame was captured. The client estimates the global camera shift since then (`cv2.phaseCorrelate` on 160 px grayscale thumbnails) and shifts the overlay to match, so masks stay on objects during camera pans. Press `m` to toggle. It compensates camera motion only, not objects moving on their own.

## Setup
One venv inside `sam2/` (gitignored). The server downloads `facebook/sam2-hiera-tiny` from Hugging Face on first start; no manual checkpoint.

- **Mac (Apple Silicon):**
  `cd sam2 && python3.12 -m venv .venv && . .venv/bin/activate && pip install torch torchvision && SAM2_BUILD_CUDA=0 pip install -e . && pip install -r requirements-ws.txt huggingface_hub`
- **Windows + RTX 4060:**
  `cd sam2 && python -m venv .venv && .venv\Scripts\activate && pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121 && pip install -e . && pip install -r requirements-ws.txt huggingface_hub`

## Devices
The server picks the device at startup and prints it (`Using device=...`):
- **CUDA:** bfloat16/float16 under autocast (original behaviour).
- **Apple Silicon (MPS):** float16 autocast by default; `SAM2_MPS_DTYPE=float32` opts out. `PYTORCH_ENABLE_MPS_FALLBACK=1` covers ops without MPS kernels.
- **CPU:** float32; works but slow.

The server runs a warm-up (one click frame + two tracking frames) before accepting clients, so the first real frame is fast. Every 50 frames it logs mean frame time, object count, and device memory (`[stats] ...`).

Environment variables:
- `SAM2_WS_PORT` (server, default `8765`). The voice coordinator (`provider/coordinator`) also uses 8765; run one at a time or move one.
- `SAM2_WS_URL` (client, default `ws://localhost:8765`), e.g. a Mac client against a 4060 server: `SAM2_WS_URL=ws://<4060-ip>:8765 python sam2_ws_client.py`.
- `SAM2_MPS_DTYPE` (server, Mac only): `float16` (default) or `float32`.
- `SAM2_MEMORY` (server): which past frames each object keeps. `sparse` (default: 1, 3, 5 frames back), `full` (1–15, everything the model can read), `light` (1, 2). The model skips missing frames and keeps correct time encodings for the rest, so this trades a little tracking robustness for speed.

## Performance (M4 Max, tiny model, `bench_ws.py`, 612x408 image)
Where a tracked frame's time goes (float16, 1 object): memory attention 85 ms, image encoder 48 ms (once per frame), mask heads + memory encoder 11 ms, JPEG/PNG/upsample < 2 ms. Memory attention is paid per object, so it dominates with several objects.

Per tracked frame, server round-trip, static image:

| | 1 object | 2 objects | memory over 300 frames |
| --- | --- | --- | --- |
| original (float32, no pruning) | 327 ms p50, creeping 262→326 | 618 ms p50, creeping 421→610 | grows every frame |
| + memory pruning | 289 ms, flat | 510 ms, flat | flat (455 MB) |
| + float16 on MPS | 148 ms (6.8 FPS) | 238 ms (4.2 FPS) | flat (390 MB) |
| + `SAM2_MEMORY=sparse` (default) | — | 158 ms (6.3 FPS) | flat (368 MB) |

### Object count sets the frame budget

Measured 2026-09-20 on the pan sequence (`--pan --frames 180`, MPS float16,
`SAM2_MEMORY=sparse`), adding objects to the same session:

| objects | p50 | p95 | achieved | masks held on their clicked point |
| --- | --- | --- | --- | --- |
| 1 | 111 ms | 115 ms | 8.9 FPS | 179/179 |
| 2 | 167 ms | 172 ms | 5.9 FPS | 179/179 each |
| 3 | 266 ms | 279 ms | 3.7 FPS | 179/179 each |
| 4 | 329 ms | 344 ms | 3.0 FPS | 179/179 each |

Roughly +70 ms per object, and tracking quality does not degrade as objects are
added. **This is why selection is capped at 3** (`MAX_TRACKED_OBJECTS` in
`provider/coordinator/planner.py`): the Quest stream's default 3 fps gives a
333 ms budget per frame, which 3 objects fit with headroom and 4 do not. Past
the budget the steady state merely drops frames, but the post-seed catch-up
starts failing with "SAM 2 could not catch up. Lower Stream FPS and try again."
Frame time also scales with mask area, so a large object costs more than a small
one: the same 3-object run with a small third mask measured 226 ms.

Panning sequence (`--pan`, 180 frames): `full` 147 ms (1 obj) / 241 ms (2 obj) vs `sparse` 111 ms / 195 ms. Masks stayed on every clicked point in 179/179 frames; per-frame IoU sparse vs full ≥ 0.986. Static masks vs the original code: IoU ≥ 0.991. First frame after connect: 1.4–4 s originally, ~0.1 s now.

Client, simulated 1080p webcam, 1 object: 3.7 mask updates/s with the old client (fixed 100 ms sleep, full-resolution frames) vs 6.4/s now. Motion compensation on the pan sequence: the mask's IoU with the object's current position goes from 0.937 → 0.989 at 3 frames of display lag (~100 ms) and 0.885 → 0.991 at 6 frames (~200 ms), at 1.9 ms per displayed 1080p frame. The 4060 has not been benchmarked with these changes yet.

Benchmark without a webcam (works on both machines):
- `python bench_ws.py --image ../assets/laptop.jpg --click 379,294 --click 75,305 --frames 300 --save-masks outputs/run_a`
- `python bench_ws.py --image ../assets/laptop.jpg --click 379,294 --pan --frames 180 --save-masks outputs/pan_a` (simulated camera pan; reports how often each mask covers its clicked point)
- `python bench_ws.py --compare outputs/run_a outputs/run_b` (mask IoU between two runs; per-frame for pan runs)

## How to Verify
1. Ensure the SAM 2 environment is active (`.venv`) and the `requirements-ws.txt` dependencies are installed.
2. Run the server: `cd sam2 && python sam2_ws_server.py`. Wait for `Starting SAM 2 WebSocket Server`.
3. In a separate terminal, run the client: `cd sam2 && python sam2_ws_client.py`.
4. The webcam will open. Left-click to add distinct tracked objects. Validate that the camera maintains a stable FPS on screen. Restart the client to clear objects (removal isn't implemented).

## Quest + voice automatic initialization

This opt-in integration replaces manual clicking with Huawei-selected
**interior points**. Quest supplies RGB and push-to-talk audio. Huawei sees one
selected JPEG plus that audio; the existing SAM 2 server gets the exact JPEG
and one normal `clicks` entry per selected object, then subsequent Quest images
with empty `clicks`. It does not call Huawei per video frame.

Up to `MAX_TRACKED_OBJECTS` (3) objects are tracked at once, all clicked on the
one selected frame so a single SAM 2 session holds them together. `obj_id` runs
1..N in the model's own order. Points closer than 0.05 normalised distance are
treated as the same object named twice and dropped, and if SAM 2 fails to start
any requested object the whole selection errors rather than quietly tracking a
smaller set. A new utterance replaces the whole previous selection, not one
object of it.

For Python server commands, Unity configuration, USB/Wi-Fi connectivity,
microphone permissions, controls, and troubleshooting, follow
[Quest camera + push-to-talk setup](quest-audio-setup.md).

### Unity result handoff

`CoordinatorClient.TrackingResultReceived` is the **main-thread handoff to the
existing visualization code**. `LatestTrackingResult` also exposes the latest
result. It carries `frame_id`, `seed_frame_id`, `width`, `height`, `generation`,
`stage_epoch`, and `objects[]` with the existing `obj_id`/`mask_b64` fields plus
`label`, the name the model gave that object (null when it named none; SAM 2
itself knows only `obj_id`s, so the bridge re-attaches these). `objects[]` holds
one entry per tracked object, so a renderer must handle more than one.
`RawPayloadJson` includes the original capture envelope. For example, attach
your existing renderer by subscribing once the client exists:

```csharp
// SpatialRuntime creates its client after the camera starts and an endpoint is set.
var client = FindAnyObjectByType<SpatialRuntime>().Coordinator;
// In your renderer: subscribe when client becomes non-null; unsubscribe on disable.
client.TrackingResultReceived += OnTrackingResult;
// OnTrackingResult(TrackingResult result) consumes result.objects[n].mask_b64.
```

This integration delivers masks to that hook; it does not implement a new
passthrough renderer. The webcam client's existing markers/labels are OpenCV
rendering on the computer, so they do not automatically become Unity objects.
Use `TrackingStatusReceived` to clear/fade visuals on `selecting`, `error`,
`stopped`, or disconnection. Never treat a pixel mask as world coordinates.

### Frame and queue rules

- Tracking explicitly enables continuous capture (default 3 fps); the old
  event-driven 2 fps drawing contract still applies to spatial-mark mode.
- An utterance ends with its selected `frame_id`. The server waits up to 5 s
  for that JPEG if control messages overtake it. The same immutable bytes go
  to Huawei and to SAM 2. No additional resize occurs in the bridge.
- UVs use the existing top-left/pixel-centre convention: `x=u*w-0.5`,
  `y=v*h-0.5`, clamped at image edges. Only a foreground point is requested;
  bounding-box centroids are not substituted for foreground points.
- Capture retains matched pose/intrinsics. Image streaming does not require
  a depth hit and does not populate the spatial-mark single-ray cache.
- While selection runs, a CPU JPEG history is bounded to **20 s, 24 MiB,
  and 160 frames**. SAM 2 starts a fresh session at the selected frame and
  replays successors in order. It then switches to one frame in flight,
  latest-frame mode. Expired history or a catch-up timeout asks for a new
  selection instead of silently applying stale coordinates to a current frame.
- The selected snapshot is reliable/queued; ordinary outgoing video is
  replaceable. Audio chunks and their end marker share FIFO ordering.
- Session disconnect, tracking-origin reset, B-button stop, and replacement
  requests fence old work. Fixed image dimensions are required per stream.
- `Flip Image Vertically` is a diagnostic option. Check an asymmetric scene
  in the received image first; if changed, its inverse is recorded in `crop`.
  `LateUpdate` uses PCA's `GetColors()` (ordered GPU readback after its native
  camera update) and CPU downsampling. A direct `Graphics.Blit(GetTexture())`
  can read the previous image in MRUK 205 and is deliberately avoided.
  Readback is synchronous in this first implementation; lower Stream
  FPS if it causes device hitches. Raising FPS above tracker throughput makes
  catch-up fail, rather than making tracking faster.

### Integration verification

Offline tests, working directory `provider/`:

```bash
python -m unittest discover -s tests -v
```

For on-device checks and the headset-free smoke, see
[Quest setup verification](quest-audio-setup.md#how-to-verify).
The offline suite delays selection and checks exact JPEG identity,
ordered replay, wrong-frame rejection, cancellation, and bounded history. For
multi-object selection it also checks that several points are parsed, deduped
and capped, that both objects are clicked on the one seed frame and tracked
without re-clicking, and that a half-started selection raises instead of
silently tracking fewer objects.

Multi-object verification (2026-09-20): **209 Python tests** passed, and the
timing table above was measured against the real MPS server. **Rendering more
than one mask in the headset has not been run on device**; per-object colours,
depths, and labels are Unity-side changes that only a headset run can confirm.

Verification recorded for this integration (2026-09-19): **82 Python tests**
passed; **28 Unity EditMode tests** passed with Android selected, and the four
input/transport tests passed again after the final camera-readback change.
A stub-coordinator smoke against the real MPS SAM 2 server returned ten masks
from repeated camera-fixture frames without clicks. **Physical Quest capture,
microphone behavior, and live Huawei object-selection accuracy still require
the on-device run in the setup guide**; the smoke used synthesized audio and
no cloud calls.

The original webcam fallback still runs from `sam2/`:

```bash
SAM2_WS_URL=ws://localhost:8766 python sam2_ws_client.py
```
