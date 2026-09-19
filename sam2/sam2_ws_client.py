import base64
import json
import os
import threading
import time

import cv2
import numpy as np
from websockets.sync.client import connect

# The model resizes every frame to 1024x1024, so sending more pixels than that
# only costs JPEG encode/decode and a larger mask round-trip.
SEND_MAX_SIDE = 1024
MASK_ALPHA = 0.5
COLORS = [(0, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 255), (255, 0, 255)]

# Shared global state
latest_frame = None
latest_overlay = None  # (colored_mask, [(text, (x, y), color)], object_count)
click_points = []
next_obj_id = 1
running = True

# Lock to protect shared variables
lock = threading.Lock()

def mouse_click(event, x, y, flags, param):
    global next_obj_id
    if event == cv2.EVENT_LBUTTONDOWN:
        with lock:
            click_points.append((x, y, next_obj_id))
            next_obj_id += 1

def build_overlay(objects, display_shape):
    """Decode masks once per server result into a colour layer plus ID labels."""
    h, w = display_shape[:2]
    colored_mask = np.zeros((h, w, 3), dtype=np.uint8)
    labels = []
    for obj in objects:
        mask_b64 = obj.get("mask_b64")
        if not mask_b64:
            continue
        mask = cv2.imdecode(np.frombuffer(base64.b64decode(mask_b64), np.uint8), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            continue
        if mask.shape[:2] != (h, w):
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        color = COLORS[obj["obj_id"] % len(COLORS)]
        colored_mask[mask > 127] = color
        x, y, bw, bh = cv2.boundingRect(mask)
        if bw and bh:
            labels.append((f"ID: {obj['obj_id']}", (x, max(y - 10, 15)), color))
    return colored_mask, labels, len(objects)

def network_worker(uri):
    global latest_frame, latest_overlay, running, click_points

    print(f"[Network] Connecting to {uri}...")
    try:
        with connect(uri, max_size=None) as websocket:
            print("[Network] Connected to SAM 2 Server!")

            while running:
                # 1. Grab the latest frame and pending clicks safely. The UI thread
                # never writes into a captured frame, so no copy is needed.
                with lock:
                    frame_to_send = latest_frame
                    pending_clicks = click_points[:]
                    click_points.clear()

                if frame_to_send is None:
                    time.sleep(0.01)
                    continue

                # 2. Downscale (clicks in display pixels are scaled to match) and compress
                h, w = frame_to_send.shape[:2]
                scale = min(1.0, SEND_MAX_SIDE / max(h, w))
                if scale < 1.0:
                    small = cv2.resize(frame_to_send, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
                else:
                    small = frame_to_send
                clicks_to_send = [
                    {"x": int(x * scale), "y": int(y * scale), "obj_id": obj_id}
                    for x, y, obj_id in pending_clicks
                ]
                _, buffer = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 70])

                # 3. Construct payload and send
                payload = {
                    "type": "frame",
                    "jpeg_b64": base64.b64encode(buffer).decode('ascii'),
                    "clicks": clicks_to_send
                }

                try:
                    # 4. One frame in flight: the blocking recv is the backpressure,
                    # so the next frame goes out as soon as the server is free.
                    websocket.send(json.dumps(payload))
                    response = json.loads(websocket.recv())

                    if response.get("type") == "result":
                        # 5. Decode here, once, not in the 30 FPS UI loop
                        overlay = build_overlay(response.get("objects", []), frame_to_send.shape)
                        with lock:
                            latest_overlay = overlay
                except Exception as e:
                    print(f"[Network] Error communicating with server: {e}")
                    break

    except ConnectionRefusedError:
        print(f"[Network] Could not connect to {uri}. Is the server running?")
    except Exception as e:
        print(f"[Network] Disconnected: {e}")
    finally:
        with lock:
            running = False

def main():
    global latest_frame, running

    # Start the network thread (SAM2_WS_URL points at a server on another machine)
    uri = os.environ.get("SAM2_WS_URL", "ws://localhost:8765")
    net_thread = threading.Thread(target=network_worker, args=(uri,), daemon=True)
    net_thread.start()

    # Initialize Camera
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[Camera] Error: Could not open webcam.")
        with lock:
            running = False
        return

    cv2.namedWindow("SAM 2 Real-Time Client")
    cv2.setMouseCallback("SAM 2 Real-Time Client", mouse_click)

    print("[Camera] UI Thread started. Click to add objects!")

    prev_time = time.time()
    while running:
        current_time = time.time()
        loop_elapsed = current_time - prev_time
        fps = 1.0 / loop_elapsed if loop_elapsed > 0 else 0
        prev_time = current_time

        ret, frame = cap.read()
        if not ret:
            print("[Camera] Warning: Camera dropped a frame.")
            break

        # Push the raw frame to the background thread and take the latest overlay
        with lock:
            latest_frame = frame
            overlay = latest_overlay

        display_frame = frame
        object_count = 0
        if overlay is not None:
            colored_mask, labels, object_count = overlay
            if colored_mask.shape == frame.shape:
                display_frame = cv2.addWeighted(frame, 1, colored_mask, MASK_ALPHA, 0)
                for text, origin, color in labels:
                    cv2.putText(display_frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        if display_frame is frame:
            display_frame = frame.copy()  # never draw on the frame the network thread may be sending

        # Draw UI
        if object_count:
            cv2.putText(display_frame, f"Tracked objects: {object_count}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        cv2.putText(display_frame, f"Camera FPS: {fps:.1f}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.imshow("SAM 2 Real-Time Client", display_frame)
        if cv2.waitKey(1) & 0xFF == 27:
            with lock:
                running = False
            break

    cap.release()
    cv2.destroyAllWindows()
    print("[Camera] Shutting down...")

if __name__ == "__main__":
    main()
