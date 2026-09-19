"""Quest frames -> the existing SAM 2 click protocol. No model/GPU imports.

One bridge belongs to one Quest connection. A pinned seed and a bounded CPU
JPEG history bridge the cloud latency; SAM 2 still sees ordinary ordered
frames, with a single positive click on the first one.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

from PIL import Image

from protocol.uv import spec_uv_to_pixel_center

logger = logging.getLogger(__name__)
MAX_JPEG_BYTES = 350_000
MAX_SIDE = 1280


class TrackingError(ValueError):
    pass


@dataclass(frozen=True)
class VideoFrame:
    sequence: int
    envelope: dict
    jpeg: bytes
    received: float

    @property
    def id(self):
        return self.envelope["frame_id"]

    @property
    def size(self):
        return self.envelope["sent_w"], self.envelope["sent_h"]


def point_click(target: dict, frame: VideoFrame) -> dict:
    if not isinstance(target, dict) or target.get("type") != "image_point":
        raise TrackingError("No unambiguous image point was selected. Look at the object and try again.")
    uv = [target.get("u"), target.get("v")]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n)
           or not 0 <= n <= 1 for n in uv):
        raise TrackingError("The selected point is outside the image.")
    x, y = spec_uv_to_pixel_center(*uv, *frame.size)
    w, h = frame.size
    # Normalized edge coordinates map to half-pixels; clamp only those edges.
    return {"x": max(0.0, min(w - 1.0, x)), "y": max(0.0, min(h - 1.0, y)), "obj_id": 1}


class FrameHistory:
    def __init__(self, *, seconds=20.0, max_bytes=24 * 1024 * 1024, max_frames=160):
        self.seconds, self.max_bytes, self.max_frames = seconds, max_bytes, max_frames
        self.frames: deque[VideoFrame] = deque()
        self.bytes = 0
        self.sequence = 0
        self.changed = asyncio.Event()

    def clear(self):
        self.frames.clear()
        self.bytes = 0
        self.changed.set()

    def add(self, envelope: dict, jpeg: bytes) -> VideoFrame:
        if not jpeg or len(jpeg) > MAX_JPEG_BYTES:
            raise TrackingError("Camera JPEG is missing or exceeds 350 KB.")
        w, h = envelope["sent_w"], envelope["sent_h"]
        if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
            raise TrackingError("Camera image dimensions exceed the tracking limit.")
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                if image.format != "JPEG" or image.size != (w, h):
                    raise TrackingError("JPEG dimensions do not match its capture envelope.")
                image.load()
        except (OSError, ValueError) as exc:
            raise TrackingError("Invalid camera JPEG or capture dimensions.") from exc
        if self.frames:
            last = self.frames[-1]
            if envelope["frame_id"] == last.id or envelope["t_unix_ns"] <= last.envelope["t_unix_ns"]:
                raise TrackingError("Duplicate or out-of-order camera exposure.")
            if (w, h) != last.size:
                raise TrackingError("Camera dimensions changed; reconnect before tracking.")
        self.sequence += 1
        frame = VideoFrame(self.sequence, copy.deepcopy(envelope), bytes(jpeg), time.monotonic())
        self.frames.append(frame)
        self.bytes += len(jpeg)
        while self.frames and (len(self.frames) > self.max_frames or self.bytes > self.max_bytes
                               or frame.received - self.frames[0].received > self.seconds):
            self.bytes -= len(self.frames.popleft().jpeg)
        self.changed.set()
        return frame

    async def wait_for(self, frame_id: str, timeout=5.0) -> VideoFrame:
        async def wait():
            while True:
                self.changed.clear()
                for frame in self.frames:
                    if frame.id == frame_id:
                        return frame
                await self.changed.wait()
        try:
            return await asyncio.wait_for(wait(), timeout)
        except asyncio.TimeoutError as exc:
            raise TrackingError("The selected camera frame did not arrive. Try again.") from exc

    def successors(self, sequence: int) -> list[VideoFrame]:
        items = [frame for frame in self.frames if frame.sequence > sequence]
        if items and items[0].sequence != sequence + 1:
            raise TrackingError("Tracking history expired. Look at the object and try again.")
        return items


class Sam2Bridge:
    def __init__(self, url: str, send, *, connector=None, history=None, timeout=10.0):
        self.url, self.send = url, send
        self.history = history or FrameHistory()
        self.connector = connector
        self.timeout = timeout
        self.generation = 0
        self.task = None
        self.turn_id = 0
        self.utterance_id = None
        self.epoch = None

    async def stop(self):
        self.generation += 1
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def reset(self):
        await self.stop()
        self.history.clear()

    async def status(self, state: str, text: str, **extra):
        await self.send("tracking_status", self.turn_id, {
            "state": state, "text": text, "generation": self.generation, **extra,
        }, self.utterance_id)

    async def begin(self, turn_id: int, utterance_id: str):
        await self.stop()
        self.turn_id, self.utterance_id = turn_id, utterance_id
        await self.status("selecting", "Selecting the requested object…")
        return self.generation

    async def seed(self, frame: VideoFrame, target: dict, generation: int):
        if generation != self.generation:
            return
        click = point_click(target, frame)
        if time.monotonic() - frame.received > self.history.seconds:
            raise TrackingError("The selected camera frame expired. Try again.")
        if self.epoch != frame.envelope["stage_epoch"]:
            raise TrackingError("Tracking origin changed. Look at the object and try again.")
        self.history.successors(frame.sequence)  # fail before opening the GPU session
        logger.info("SAM2 seed frame_id=%s x=%.1f y=%.1f obj_id=1", frame.id, click["x"], click["y"])
        self.task = asyncio.create_task(self._run(frame, click, generation))

    async def _run(self, seed: VideoFrame, click: dict, generation: int):
        import websockets

        connect = self.connector or websockets.connect
        try:
            await self.status("initializing", "Initializing SAM 2…", seed_frame_id=seed.id)
            async with connect(self.url, max_size=4 * 1024 * 1024, open_timeout=self.timeout) as ws:
                frame = seed
                catching_up = True
                started = time.monotonic()
                while generation == self.generation:
                    response = await self._exchange(ws, frame, [click] if frame is seed else [])
                    if generation != self.generation:
                        return
                    pending = self.history.successors(frame.sequence) if catching_up else []
                    if catching_up and pending:
                        if time.monotonic() - started > self.history.seconds:
                            raise TrackingError("SAM 2 could not catch up. Lower Stream FPS and try again.")
                        frame = pending[0]
                        continue
                    if catching_up:
                        catching_up = False
                        await self.status("tracking", "SAM 2 is running.", seed_frame_id=seed.id)
                    await self.send("tracking_result", self.turn_id, {
                        "frame_id": frame.id, "seed_frame_id": seed.id,
                        "generation": generation, "stage_epoch": frame.envelope["stage_epoch"],
                        "width": frame.size[0], "height": frame.size[1],
                        "objects": response["objects"], "envelope": frame.envelope,
                    }, self.utterance_id)
                    # At steady state keep only the freshest unsent frame, just
                    # like sam2_ws_client.py. The initial catch-up is ordered.
                    while generation == self.generation:
                        self.history.changed.clear()
                        if self.history.frames and self.history.frames[-1].sequence > frame.sequence:
                            frame = self.history.frames[-1]
                            break
                        await asyncio.wait_for(self.history.changed.wait(), self.timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("SAM2 stream stopped: %s", exc)
            if generation == self.generation:
                text = str(exc) if isinstance(exc, TrackingError) else "SAM 2 disconnected or timed out. Check the server and try again."
                await self.status("error", text)

    async def _exchange(self, ws, frame: VideoFrame, clicks: list) -> dict:
        await asyncio.wait_for(ws.send(json.dumps({
            "type": "frame", "frame_id": frame.id,
            "jpeg_b64": base64.b64encode(frame.jpeg).decode("ascii"), "clicks": clicks,
        })), self.timeout)
        response = json.loads(await asyncio.wait_for(ws.recv(), self.timeout))
        if response.get("type") != "result" or not isinstance(response.get("objects"), list):
            raise TrackingError("SAM 2 returned an invalid result.")
        # Older servers have no frame_id. One request in flight still provides
        # unambiguous correlation, retaining compatibility with the webcam demo.
        if response.get("frame_id", frame.id) != frame.id:
            raise TrackingError("SAM 2 returned a result for the wrong frame.")
        if clicks and not any(obj.get("obj_id") == 1 and obj.get("mask_b64") for obj in response["objects"]):
            raise TrackingError("SAM 2 did not initialize the selected object.")
        return response
