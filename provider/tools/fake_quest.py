#!/usr/bin/env python3
"""Headset-free Quest stand-in for one voice turn over the real WebSocket.

Run the coordinator, then this, both from provider/:
    python -m coordinator.server --planner stub      # or yibu (spends credit)
    python -m tools.fake_quest --say "mark the laptop" --jpeg photo.jpg

Sends hello → frame (fixture envelope + JPEG) → ~100 ms audio_chunks →
utterance_end, ACKs every scene_op, and prints a millisecond timeline until
`speak`. --reject / --no-ack exercise the honesty paths.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import websockets

from protocol.ids import new_ulid
from protocol.validate import load_fixture
from voice.audio import BYTES_PER_SECOND, read_wav_pcm, say_to_pcm

CHUNK_BYTES = BYTES_PER_SECOND // 10  # ~100 ms


def wrap(msg_type: str, session_id: str | None, payload: dict, utterance_id: str | None = None, turn_id: int = 0) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": msg_type,
            "session_id": session_id,
            "turn_id": turn_id,
            "utterance_id": utterance_id,
            "payload": payload,
        }
    )


def placement_ack(op: dict, mode: str) -> dict:
    if mode == "reject":
        return {
            "op_id": op["op_id"], "turn_id": op["turn_id"], "stage_epoch": op["stage_epoch"],
            "status": "rejected", "reason": "no_surface", "drawing_id": None, "pin": None,
        }
    return {
        "op_id": op["op_id"], "turn_id": op["turn_id"], "stage_epoch": op["stage_epoch"],
        "status": "placed", "reason": None, "drawing_id": new_ulid(), "pin": "surface",
    }


async def run(args: argparse.Namespace) -> int:
    pcm = read_wav_pcm(args.wav) if args.wav else say_to_pcm(args.say)
    jpeg = args.jpeg.read_bytes() if args.jpeg else None
    envelope = load_fixture("valid", "capture_envelope.json")
    envelope["frame_id"] = new_ulid()
    envelope["t_unix_ns"] = time.time_ns()
    utterance_id = new_ulid()
    t0 = time.monotonic()

    def log(event: str, detail: str = "") -> None:
        print(f"{(time.monotonic() - t0) * 1000:8.0f} ms  {event:<14} {detail}", flush=True)

    async with websockets.connect(args.url, max_size=None) as ws:
        await ws.send(wrap("hello", None, {
            "device": "fake_quest", "app": "QuestDemo", "os_version": "n/a",
            "capabilities": {"pca": False, "depth": False, "tts": False},
        }))
        hello_ok = json.loads(await ws.recv())
        session_id = hello_ok["payload"]["session_id"]
        log("hello_ok", session_id)

        frame_payload = {"utterance_id": utterance_id, "envelope": envelope}
        if jpeg is not None:
            frame_payload["jpeg_b64"] = base64.b64encode(jpeg).decode("ascii")
        await ws.send(wrap("frame", session_id, frame_payload, utterance_id))
        log("frame", f"jpeg={len(jpeg) if jpeg else 0}B frame_id={envelope['frame_id']}")

        for i in range(0, len(pcm), CHUNK_BYTES):
            chunk = pcm[i : i + CHUNK_BYTES]
            await ws.send(wrap("audio_chunk", session_id, {
                "utterance_id": utterance_id, "t_unix_ns": time.time_ns(),
                "audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1,
                          "data_b64": base64.b64encode(chunk).decode("ascii")},
            }, utterance_id))
            if args.realtime:
                await asyncio.sleep(len(chunk) / BYTES_PER_SECOND)
        await ws.send(wrap("utterance_end", session_id, {"utterance_id": utterance_id, "t_unix_ns": time.time_ns()}, utterance_id))
        log("utterance_end", f"audio={len(pcm)}B ({len(pcm) / BYTES_PER_SECOND:.2f}s)")

        mode = "none" if args.no_ack else ("reject" if args.reject else "place")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), deadline - time.monotonic())
            except asyncio.TimeoutError:
                break
            message = json.loads(raw)
            kind, payload = message["type"], message["payload"]
            if kind == "scene_op":
                target = payload.get("target", {})
                log("scene_op", f"{payload['kind']} {target.get('type')} u={target.get('u')} v={target.get('v')}")
                if mode != "none":
                    ack = placement_ack(payload, mode)
                    await ws.send(wrap("ack", session_id, ack, None, payload["turn_id"]))
                    log("ack →", ack["status"])
            elif kind == "speak":
                log("speak", json.dumps(payload["text"], ensure_ascii=False))
                return 0
            else:
                log(kind, json.dumps(payload, ensure_ascii=False)[:120])
        log("timeout", "no speak received")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--wav", type=Path, help="16 kHz mono s16 WAV")
    src.add_argument("--say", help="synthesize this text with macOS say")
    parser.add_argument("--jpeg", type=Path, help="camera frame to send with the utterance")
    parser.add_argument("--url", default="ws://localhost:8765")
    parser.add_argument("--reject", action="store_true", help="ACK every op as rejected/no_surface")
    parser.add_argument("--no-ack", action="store_true", help="never ACK (exercises the 1.5 s timeout)")
    parser.add_argument("--realtime", action="store_true", help="pace audio chunks at real time")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
