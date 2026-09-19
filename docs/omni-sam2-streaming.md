# SAM 2 Streaming Architecture

## What it is
A decoupled client-server architecture for real-time SAM 2 tracking. 
- **Server** (`sam2/sam2_ws_server.py`): Hosts the SAM 2 PyTorch model locally on CUDA. It asynchronously accepts base64-encoded frames and click coordinates over WebSockets, and returns base64-encoded segmentation masks.
- **Client** (`sam2/sam2_ws_client.py`): A Python-based proof-of-concept for the eventual Quest 3S Unity app. It captures webcam video at 30+ FPS, sends the newest frame to the server over WebSockets (capped at 10 FPS), and overlays the returned segmentation mask without blocking the main rendering loop.

## Contract / Rules
- **Dependencies**: The architecture requires `websockets>=11.0` and `opencv-python>=4.8.0`. Install them via `pip install -r sam2/requirements-ws.txt`.
- **Payload Format**: 
  - Client sends `{"type": "frame", "jpeg_b64": <string>, "clicks": [{"x": int, "y": int, "obj_id": int}], "removes": [int or "ALL"]}`.
  - Server replies `{"type": "result", "objects": [{"obj_id": int, "mask_b64": <string>}]}`.
- **Memory Safety**: The server operates on a rolling window. It automatically nullifies raw image tensors older than 5 frames in its streaming state to prevent CUDA Out of Memory exceptions. The client strictly caps network updates to ~10 FPS to prevent buffer bloat.
- **Multi-Threading**: The client uses a 3-thread design. The main UI thread renders the screen, a dedicated hardware thread captures the camera (to avoid blocking on native hardware limits), and a background network thread handles WebSocket communication.

## How to Verify
1. Ensure the SAM 2 environment is active (`.venv`) and the `requirements-ws.txt` dependencies are installed.
2. Run the server: `cd sam2 && python sam2_ws_server.py`. Wait for the "model globally loaded" message.
3. In a separate terminal, run the client: `cd sam2 && python sam2_ws_client.py`.
4. The webcam will open. Left-click to add distinct glowing tracked objects. Right-click an object to remove it, or right-click the background to clear all objects. Validate that the camera maintains a stable FPS on screen.
