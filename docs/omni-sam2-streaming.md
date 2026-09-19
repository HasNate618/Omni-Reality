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
- **Memory Safety**: The server keeps a rolling window. It drops raw image tensors older than 5 frames, and tracking memory (`non_cond_frame_outputs`) older than 16 frames per object, which is all the model reads (6 memory frames, 15 object pointers). Clicked (conditioning) frames are kept. Device memory stays flat over long sessions.
- **Backpressure**: One frame in flight. The client waits for each result before sending the newest frame, so frames never queue up and there is no fixed sleep.
- **Multi-Threading**: The client uses two threads: the main UI thread captures the camera and renders; a background network thread encodes, sends, and decodes results.

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

## Performance (M4 Max, tiny model, `bench_ws.py`, 612x408 image)
Per tracked frame, server round-trip:

| | 1 object | 2 objects | memory over 300 frames |
| --- | --- | --- | --- |
| original (float32, no pruning) | 327 ms p50, creeping 262→326 | 618 ms p50, creeping 421→610 | grows every frame |
| + memory pruning | 289 ms, flat | 510 ms, flat | flat (455 MB) |
| + float16 on MPS (default) | 148 ms (6.8 FPS) | 238 ms (4.2 FPS) | flat (390 MB) |

Masks are identical across all three (IoU 1.000 against the original). First frame after connect: 1.4–4 s before, ~0.1 s now.

Client, same server, simulated 1080p webcam, 1 object: 3.7 mask updates/s with the old client (fixed 100 ms sleep, full-resolution frames) vs 6.4/s now; the camera view no longer re-decodes masks every frame. The 4060 has not been benchmarked with these changes yet.

Benchmark without a webcam (works on both machines):
- `python bench_ws.py --image ../assets/laptop.jpg --click 379,294 --click 75,305 --frames 300 --save-masks outputs/run_a`
- `python bench_ws.py --compare outputs/run_a outputs/run_b` (mask IoU between two runs)

## How to Verify
1. Ensure the SAM 2 environment is active (`.venv`) and the `requirements-ws.txt` dependencies are installed.
2. Run the server: `cd sam2 && python sam2_ws_server.py`. Wait for `Starting SAM 2 WebSocket Server`.
3. In a separate terminal, run the client: `cd sam2 && python sam2_ws_client.py`.
4. The webcam will open. Left-click to add distinct tracked objects. Validate that the camera maintains a stable FPS on screen. Restart the client to clear objects (removal isn't implemented).
