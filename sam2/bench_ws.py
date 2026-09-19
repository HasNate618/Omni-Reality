"""Headless benchmark for sam2_ws_server.py (no webcam, no GUI).

Frame 0 carries one click per object; later frames are tracking-only.
Reports round-trip latency, achieved FPS, and per-object mask stats. Runs
the same on a Mac and on the 4060 box.

Static (same image every frame):
    python bench_ws.py --image ../assets/laptop.jpg --click 379,294 --click 75,305 --frames 300
Panning (a crop slides across a 1.25x upscale, like a camera pan; reports how
often each mask still covers its clicked point):
    python bench_ws.py --image ../assets/laptop.jpg --click 379,294 --pan --frames 180
Compare two runs saved with --save-masks (static: last frame; pan: every frame):
    python bench_ws.py --compare outputs/run_a outputs/run_b
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

PAN_PX = 150  # max horizontal travel of the pan, in upscaled-image pixels
PAN_LEG = 45  # frames per leg (there and back)


def decode_mask(mask_b64):
    return cv2.imdecode(np.frombuffer(base64.b64decode(mask_b64), np.uint8), cv2.IMREAD_GRAYSCALE)


def iou(a, b):
    union = np.logical_or(a, b).sum()
    return np.logical_and(a, b).sum() / union if union else 1.0


def compare(dir_a, dir_b):
    dir_a, dir_b = Path(dir_a), Path(dir_b)
    if (dir_a / "pan_masks.npz").exists():
        a, b = np.load(dir_a / "pan_masks.npz"), np.load(dir_b / "pan_masks.npz")
        for key in a.files:
            scores = [iou(x, y) for x, y in zip(a[key], b[key])]
            print(f"{key}: per-frame IoU mean {np.mean(scores):.3f} min {np.min(scores):.3f}")
        return
    for path_a in sorted(dir_a.glob("obj*.png")):
        a = cv2.imread(str(path_a), cv2.IMREAD_GRAYSCALE) > 0
        b = cv2.imread(str(dir_b / path_a.name), cv2.IMREAD_GRAYSCALE) > 0
        print(f"{path_a.stem}: IoU {iou(a, b):.3f}")


class Pan:
    """Crop of a ZOOMx upscale sliding horizontally, keeping every clicked point in view."""

    ZOOM = 1.25
    MARGIN = 10

    def __init__(self, image, points):
        z = self.ZOOM
        self.big = cv2.resize(image, (round(image.shape[1] * z), round(image.shape[0] * z)))
        self.h, self.w = image.shape[:2]
        xs = [p[0] * z for p in points]
        ys = [p[1] * z for p in points]
        max_x0 = self.big.shape[1] - self.w
        lo = max(0, int(max(xs)) + self.MARGIN - self.w + 1)
        hi = min(max_x0, int(min(xs)) - self.MARGIN)
        if lo > hi:
            raise SystemExit("clicked points are too far apart to stay in view during the pan")
        self.x_start, self.travel = lo, min(PAN_PX, hi - lo)
        centre_y = int(sum(ys) / len(ys))
        self.y0 = int(np.clip(centre_y - self.h // 2, 0, self.big.shape[0] - self.h))

    def x0(self, i):
        phase = (i / PAN_LEG) % 2
        return self.x_start + int(self.travel * (1 - abs(phase - 1)))

    def frame(self, i):
        x0 = self.x0(i)
        return np.ascontiguousarray(self.big[self.y0 : self.y0 + self.h, x0 : x0 + self.w])

    def to_frame(self, i, point):
        """Original-image point -> pixel in frame i (None if out of view)."""
        x = int(point[0] * self.ZOOM) - self.x0(i)
        y = int(point[1] * self.ZOOM) - self.y0
        return (x, y) if 0 <= x < self.w and 0 <= y < self.h else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image")
    parser.add_argument("--click", action="append", default=[], help="x,y in image pixels; one object each")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--pan", action="store_true", help="simulate a camera pan instead of a static image")
    parser.add_argument("--url", default="ws://localhost:8765")
    parser.add_argument("--save-masks", help="directory for masks (static: last frame; pan: all frames)")
    parser.add_argument("--compare", nargs=2, metavar=("DIR_A", "DIR_B"), help="mask IoU between two runs")
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return

    image = cv2.imread(args.image)
    points = [tuple(int(v) for v in c.split(",")) for c in args.click]
    pan = Pan(image, points) if args.pan else None

    def jpeg_b64(i):
        frame = pan.frame(i) if pan else image
        return base64.b64encode(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1]).decode("ascii")

    static_b64 = None if pan else jpeg_b64(0)
    clicks = []
    for obj_id, point in enumerate(points, start=1):
        x, y = pan.to_frame(0, point) if pan else point
        clicks.append({"x": int(x), "y": int(y), "obj_id": obj_id})

    latencies, pan_masks = [], {}
    covered = {c["obj_id"]: [0, 0] for c in clicks}  # [hits, frames in view]
    last_objects = []
    started = time.monotonic()
    with connect(args.url, max_size=None) as ws:
        for i in range(args.frames):
            payload = {"type": "frame", "jpeg_b64": static_b64 or jpeg_b64(i), "clicks": clicks if i == 0 else []}
            t = time.monotonic()
            ws.send(json.dumps(payload))
            last_objects = json.loads(ws.recv())["objects"]
            latencies.append((time.monotonic() - t) * 1000)
            if i == 0:
                print(f"first frame (clicks): {latencies[0]:.0f} ms")
            if pan and i > 0:
                for obj in last_objects:
                    mask = decode_mask(obj["mask_b64"]) > 0
                    pan_masks.setdefault(f"obj{obj['obj_id']}", []).append(mask)
                    where = pan.to_frame(i, points[obj["obj_id"] - 1])
                    if where is not None:
                        covered[obj["obj_id"]][1] += 1
                        covered[obj["obj_id"]][0] += bool(mask[where[1], where[0]])
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
    if pan:
        for obj_id, (hits, seen) in covered.items():
            print(f"obj {obj_id}: mask on clicked point in {hits}/{seen} in-view frames")
        if out_dir:
            np.savez_compressed(out_dir / "pan_masks.npz", **{k: np.stack(v) for k, v in pan_masks.items()})
        return
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
