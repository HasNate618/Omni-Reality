"""Minimal webcam overlay for eyeball checks (manual verification only)."""

import argparse
import base64
import io
import json
import queue
import threading
import time

import cv2
import numpy as np
import websockets.sync.client
from PIL import Image

from sam2ws import protocol

SESSION = "viewer"


class State:
    def __init__(self):
        self.slot = None  # newest (w, h, jpeg) to send
        self.masks = {}  # obj_id -> (mask_bool_2d, color)
        self.next_obj = 1
        self.status = "connecting"
        self.fps = 0.0
        self.pending_click = None  # (x, y) set by --click or mouse
        self.lock = threading.Lock()


COLORS = [(60, 220, 60), (60, 180, 255), (255, 120, 200), (255, 220, 60)]


def decode_mask(obj):
    raw = base64.b64decode(obj["mask_png_b64"])
    arr = np.array(Image.open(io.BytesIO(raw)).convert("L")) > 0
    return arr


def net_thread(url, state, stop):
    clicks = []
    frame_id = 0
    try:
        with websockets.sync.client.connect(
                url, max_size=protocol.MAX_JPEG_BYTES * 4) as sock:
            state.status = "live"
            last_send = 0.0
            while not stop.is_set():
                with state.lock:
                    slot = state.slot
                    pending = state.pending_click
                    state.pending_click = None
                if pending is not None:
                    x, y = pending
                    clicks.append({"x": x, "y": y, "label": 1,
                                   "obj_id": state.next_obj})
                    state.next_obj += 1
                now = time.monotonic()
                if slot is None or now - last_send < 0.1:
                    time.sleep(0.01)
                    continue
                last_send = now
                w, h, jpeg = slot
                req = protocol.build_request(
                    SESSION, frame_id, time.time_ns(), w, h, jpeg,
                    clicks, {"obj_ids": [], "all": False})
                clicks = []
                try:
                    sock.send(json.dumps(req))
                    reply = json.loads(sock.recv(timeout=5))
                except Exception as e:  # noqa: BLE001
                    state.status = f"net: {type(e).__name__}"
                    continue
                frame_id += 1
                if reply.get("type") == "result":
                    fresh = {}
                    for i, obj in enumerate(reply.get("objects", [])):
                        try:
                            fresh[obj["obj_id"]] = (
                                decode_mask(obj), COLORS[i % len(COLORS)])
                        except Exception:
                            pass
                    with state.lock:
                        state.masks = fresh
                elif reply.get("type") == "error":
                    state.status = f'server: {reply.get("code")}'
    except Exception as e:  # noqa: BLE001
        state.status = f"conn: {type(e).__name__}"


def on_mouse(event, x, y, _flags, state):
    if event == cv2.EVENT_LBUTTONDOWN:
        with state.lock:
            state.pending_click = (x, y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8767")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--click", default=None,
                    help="x,y startup click (headless smoke only)")
    args = ap.parse_args()

    state = State()
    if args.click:
        x, y = (int(v) for v in args.click.split(","))
        state.pending_click = (x, y)
    stop = threading.Event()
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {args.camera}")
    cv2.namedWindow("sam2")
    cv2.setMouseCallback("sam2", on_mouse, state)
    thread = threading.Thread(target=net_thread, args=(args.url, state, stop),
                              daemon=True)
    thread.start()
    n, t0 = 0, time.monotonic()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                state.status = "no frame"
                time.sleep(0.1)
                continue
            h, w = frame.shape[:2]
            _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with state.lock:
                state.slot = (w, h, jpeg.tobytes())
                masks = dict(state.masks)
                status = state.status
            view = frame.copy()
            for _oid, (m, color) in masks.items():
                if m.shape != (h, w):
                    m = cv2.resize(m.astype(np.uint8), (w, h)).astype(bool)
                view[m] = (view[m] * 0.5 + np.array(color) * 0.5).astype(np.uint8)
            n += 1
            el = time.monotonic() - t0
            if el >= 2.0:
                state.fps = n / el
                n, t0 = 0, time.monotonic()
                print(f"[viewer] {status} {state.fps:.0f}fps "
                      f"objs={len(masks)}", flush=True)
            cv2.putText(view, f"{status} {state.fps:.0f}fps", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imshow("sam2", view)
            if cv2.waitKey(30) & 0xFF == 27:
                break
    finally:
        stop.set()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
