"""Headless benchmark for sam2_ws_server.py (no webcam, no GUI).

Sends one image repeatedly: frame 0 carries one click per object, later
frames are tracking-only. Reports round-trip latency, achieved FPS, and
per-object mask stats. Runs the same on a Mac and on the 4060 box.

    python bench_ws.py --image ../assets/laptop.jpg --click 379,294 --click 75,305 --frames 300
    python bench_ws.py ... --save-masks outputs/fp32      # then compare with --compare
    python bench_ws.py --compare outputs/fp32 outputs/fp16
"""

import argparse
import base64
import json
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
from websockets.sync.client import connect


def decode_mask(mask_b64):
    return cv2.imdecode(np.frombuffer(base64.b64decode(mask_b64), np.uint8), cv2.IMREAD_GRAYSCALE)


def compare(dir_a, dir_b):
    for path_a in sorted(Path(dir_a).glob("obj*.png")):
        a = cv2.imread(str(path_a), cv2.IMREAD_GRAYSCALE) > 0
        b = cv2.imread(str(Path(dir_b) / path_a.name), cv2.IMREAD_GRAYSCALE) > 0
        union = np.logical_or(a, b).sum()
        iou = np.logical_and(a, b).sum() / union if union else 1.0
        print(f"{path_a.stem}: IoU {iou:.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image")
    parser.add_argument("--click", action="append", default=[], help="x,y in image pixels; one object each")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--url", default="ws://localhost:8765")
    parser.add_argument("--save-masks", help="directory for the last frame's masks")
    parser.add_argument("--compare", nargs=2, metavar=("DIR_A", "DIR_B"), help="mask IoU between two runs")
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return

    jpeg = Path(args.image).read_bytes()
    jpeg_b64 = base64.b64encode(jpeg).decode("ascii")
    clicks = []
    for obj_id, click in enumerate(args.click, start=1):
        x, y = (int(v) for v in click.split(","))
        clicks.append({"x": x, "y": y, "obj_id": obj_id})

    latencies = []
    last_objects = []
    started = time.monotonic()
    with connect(args.url, max_size=None) as ws:
        for i in range(args.frames):
            payload = {"type": "frame", "jpeg_b64": jpeg_b64, "clicks": clicks if i == 0 else []}
            t = time.monotonic()
            ws.send(json.dumps(payload))
            last_objects = json.loads(ws.recv())["objects"]
            latencies.append((time.monotonic() - t) * 1000)
            if i == 0:
                print(f"first frame (clicks): {latencies[0]:.0f} ms")
    elapsed = time.monotonic() - started

    tracked = latencies[1:] or latencies
    q = statistics.quantiles(tracked, n=20) if len(tracked) >= 20 else [max(tracked)] * 19
    print(f"tracked frames: {len(tracked)}  p50 {statistics.median(tracked):.0f} ms  "
          f"p95 {q[18]:.0f} ms  first-10 mean {statistics.mean(tracked[:10]):.0f} ms  "
          f"last-10 mean {statistics.mean(tracked[-10:]):.0f} ms")
    print(f"achieved: {args.frames / elapsed:.2f} FPS over {elapsed:.1f} s")

    out_dir = Path(args.save_masks) if args.save_masks else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for obj in last_objects:
        mask = decode_mask(obj["mask_b64"])
        ys, xs = np.nonzero(mask)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None
        print(f"obj {obj['obj_id']}: mask {mask.shape[1]}x{mask.shape[0]}  "
              f"area {len(xs) / mask.size:.1%}  bbox {bbox}")
        if out_dir:
            cv2.imwrite(str(out_dir / f"obj{obj['obj_id']}.png"), mask)


if __name__ == "__main__":
    main()
