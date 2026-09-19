"""Headless perf driver: replays disk frames, records latency/VRAM."""

import argparse
import asyncio
import base64
import glob
import json
import os
import statistics
import time

import websockets

from sam2ws import protocol


async def run_harness(frames, clicks, server_url, max_fps=10.0, session_id="harness"):
    """frames: list of (w, h, jpeg_bytes). clicks: list of click dicts for frame 0.
    Returns dict with latencies, drops, and server cuda stats."""
    lat = []
    last = None
    gap = 1.0 / max_fps
    async with websockets.connect(server_url,
                                  max_size=protocol.MAX_JPEG_BYTES * 4) as sock:
        for i, (w, h, raw) in enumerate(frames):
            req = protocol.build_request(
                session_id, i, time.time_ns(), w, h, raw,
                clicks if i == 0 else [],
                {"obj_ids": [], "all": False},
            )
            t0 = time.monotonic()
            await sock.send(json.dumps(req))
            last = json.loads(await sock.recv())
            lat.append((time.monotonic() - t0) * 1000)
            if i < len(frames) - 1:
                await asyncio.sleep(gap)
        await sock.send(json.dumps({"v": 1, "type": "stats"}))
        stats = json.loads(await sock.recv())
    lat_sorted = sorted(lat)
    return {
        "frames_sent": len(frames),
        "mask_latency_ms": {
            "p50": round(statistics.median(lat_sorted), 1),
            "p95": round(lat_sorted[max(0, int(len(lat_sorted) * 0.95) - 1)], 1),
            "max": round(lat_sorted[-1], 1),
        },
        "last_reply": last["type"] if last else None,
        "last_objects": len(last.get("objects", [])) if last else 0,
        "server": stats,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True, help="dir of .jpg frames")
    ap.add_argument("--out", required=True, help="timings json path")
    ap.add_argument("--url", default="ws://127.0.0.1:8767")
    ap.add_argument("--click", default="320,180,1,1",
                    help="x,y,label,obj_id for frame 0")
    ap.add_argument("--size", default=None, help="WxH override, e.g. 640x360")
    args = ap.parse_args()

    from PIL import Image
    frames = []
    for path in sorted(glob.glob(os.path.join(args.frames, "*.jpg"))):
        with open(path, "rb") as f:
            raw = f.read()
        if args.size:
            w, h = (int(v) for v in args.size.split("x"))
        else:
            with Image.open(path) as im:
                w, h = im.size
        frames.append((w, h, raw))
    x, y, label, obj_id = (int(v) for v in args.click.split(","))
    clicks = [{"x": x, "y": y, "label": label, "obj_id": obj_id}]

    result = asyncio.run(run_harness(frames, clicks, args.url))
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
