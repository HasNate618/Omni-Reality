"""Measure what actually drives A-mode selection latency.

Every lever costs something, so this reports latency *and* whether the model
still found the object, for one fixed scene and utterance. Spends gateway
credit: one call per repetition.

    python -m tools.bench_tracking --reps 3
"""
from __future__ import annotations

import argparse
import asyncio
import io
import statistics
import time
from pathlib import Path

from PIL import Image

from coordinator import planner as planner_mod
from coordinator.planner import YibuPlanner
from voice.audio import say_to_pcm

FRAME = Path(__file__).resolve().parents[2] / "sam2ws/tests/data/harness_frames/000000.jpg"
REQUEST = "Track the cup on the table in front of me."
SHORT_REQUEST = "Track the cup."

# Same contract, without asking the model to transcribe the whole utterance.
MINIMAL_PROMPT = """You hear a user's recorded request and see one Quest camera image.
Select the single visible object the user asks to track/find. Reply ONLY with:
{"track":{"type":"image_point","u":0.5,"v":0.5}}
u is left-to-right and v is top-to-bottom, given as FRACTIONS of the image
between 0 and 1. Never answer in pixels: "u":320 is wrong, "u":0.5 is right.
Choose a point INSIDE the object's visible solid surface. Return "track":null
if the object is absent or the request isn't to select an object."""


def jpeg_at(max_side: int, quality: int = 70) -> tuple[bytes, tuple[int, int]]:
    im = Image.open(FRAME).convert("RGB").resize((640, 480), Image.LANCZOS)
    if max_side != 640:
        scale = max_side / 640
        im = im.resize((max_side, int(480 * scale)), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=quality)
    return out.getvalue(), im.size


async def one_call(jpeg, size, pcm, max_tokens, prompt):
    envelope = {"frame_id": "bench", "sent_w": size[0], "sent_h": size[1], "stage_epoch": 1}
    original = planner_mod.TRACKING_PROMPT
    planner_mod.TRACKING_PROMPT = prompt
    try:
        p = YibuPlanner(tracking=True, purpose="track-object", max_tokens=max_tokens)
        started = time.monotonic()
        plan = await p.plan(pcm=pcm, jpeg=jpeg, envelope=envelope, context=[])
        return int((time.monotonic() - started) * 1000), plan.tracking_target
    finally:
        planner_mod.TRACKING_PROMPT = original


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()

    audio = say_to_pcm(REQUEST)
    short_audio = say_to_pcm(SHORT_REQUEST)
    print("audio: %.2f s (short %.2f s)\n" % (len(audio) / 32000, len(short_audio) / 32000))

    configs = []
    for side in (640, 512, 400, 320):
        jpeg, size = jpeg_at(side)
        configs.append(("image %dpx (%d KB)" % (side, len(jpeg) // 1024),
                        jpeg, size, audio, 96, planner_mod.TRACKING_PROMPT))
    jpeg640, size640 = jpeg_at(640)
    configs.append(("no 'heard' in reply", jpeg640, size640, audio, 96, MINIMAL_PROMPT))
    configs.append(("max_tokens 32", jpeg640, size640, audio, 32, planner_mod.TRACKING_PROMPT))
    configs.append(("short utterance", jpeg640, size640, short_audio, 96, planner_mod.TRACKING_PROMPT))

    print("%-28s %10s %10s   %s" % ("config", "median", "spread", "found the object?"))
    print("-" * 78)
    for label, jpeg, size, pcm, max_tokens, prompt in configs:
        times, hits = [], 0
        for _ in range(args.reps):
            try:
                ms, target = await one_call(jpeg, size, pcm, max_tokens, prompt)
            except Exception as exc:
                print("%-28s  failed: %s" % (label, type(exc).__name__))
                break
            times.append(ms)
            hits += target is not None
        if not times:
            continue
        spread = "%d-%d" % (min(times), max(times)) if len(times) > 1 else "-"
        print("%-28s %8d ms %10s   %d/%d" % (
            label, int(statistics.median(times)), spread, hits, len(times)))
    print("\ncalls spent: %d" % (len(configs) * args.reps))


if __name__ == "__main__":
    asyncio.run(main())
