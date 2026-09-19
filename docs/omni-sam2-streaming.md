# SAM 2 Streaming Architecture

## What it is
A decoupled client-server architecture for real-time SAM 2 tracking. 
- **Server** (`sam2/sam2_ws_server.py`): Hosts the SAM 2 PyTorch model locally (CUDA, Apple Silicon MPS, or CPU). It asynchronously accepts base64-encoded frames and click coordinates over WebSockets, and returns base64-encoded segmentation masks.
- **Client** (`sam2/sam2_ws_client.py`): A Python-based proof-of-concept for the eventual Quest 3S Unity app. It captures webcam video at 30+ FPS, sends the newest frame (downscaled to at most 1024 px on the long side) as soon as the previous result arrives, and overlays the returned masks without blocking the main rendering loop. Masks are decoded once per result in the network thread, not per displayed frame.

## Contract / Rules
- **Dependencies**: The architecture requires `websockets>=11.0` and `opencv-python>=4.8.0`. Install them via `pip install -r sam2/requirements-ws.txt`.
- **Payload Format**: 
  - Client sends `{"type": "frame", "jpeg_b64": <string>, "clicks": [{"x": int, "y": int, "obj_id": int}]}`. Click coordinates are in the pixels of the sent (downscaled) frame.
  - Server replies `{"type": "result", "objects": [{"obj_id": int, "mask_b64": <string>}]}`; masks are PNGs at the sent frame's resolution.
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
