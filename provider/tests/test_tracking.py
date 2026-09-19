"""Offline exact-frame handoff tests, including a real local SAM 2 socket double."""
import asyncio
import base64
import io
import json
import time
import unittest
from unittest import mock

from PIL import Image
import websockets

from coordinator.planner import PlanResult, YibuPlanner, parse_tracking_reply
from coordinator.sam2_bridge import FrameHistory, Sam2Bridge, TrackingError, point_click
from coordinator.server import CoordinatorState, _turn_sender, handle_connection
from protocol.ids import new_ulid
from protocol.validate import load_fixture
from tests.test_coordinator import DummyWs, make_hello, wait_for
from tests.test_turn import ONE_SECOND, UTT, audio_chunk, msg


def picture(index=1, size=(80, 60)):
    out = io.BytesIO()
    Image.new("RGB", size, (index * 30 % 255, 100, 40)).save(out, format="JPEG")
    envelope = load_fixture("valid", "capture_envelope.json")
    envelope.update(frame_id=new_ulid(), t_unix_ns=index, sent_w=size[0], sent_h=size[1])
    return envelope, out.getvalue()


def frame_message(envelope, jpeg):
    return msg("frame", {"envelope": envelope, "jpeg_b64": base64.b64encode(jpeg).decode()}, None)


class DelayedPlanner:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.input = None

    async def plan(self, **kwargs):
        self.input = kwargs
        self.entered.set()
        await self.release.wait()
        return PlanResult([], "", tracking_target={"type": "image_point", "u": .25, "v": .75})


class TrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_frame_before_or_after_end_then_replay_and_live(self):
        requests = []

        async def sam(ws):
            async for raw in ws:
                request = json.loads(raw)
                requests.append(request)
                await ws.send(json.dumps({"type": "result", "frame_id": request["frame_id"],
                                          "objects": [{"obj_id": 1, "mask_b64": "existing-mask"}]}))

        async with websockets.serve(sam, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            planner, ws = DelayedPlanner(), DummyWs()
            state = CoordinatorState(planner)
            bridge = state.tracking = Sam2Bridge(url, _turn_sender(ws, state))
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await wait_for(ws, lambda m: m["type"] == "hello_ok")
                selected, jpeg = picture(1)
                await ws.inject(audio_chunk(ONE_SECOND))
                # The control message deliberately overtakes the pinned JPEG.
                await ws.inject(msg("utterance_end", {"frame_id": selected["frame_id"]}))
                await wait_for(ws, lambda m: m["type"] == "turn_started")
                await ws.inject(frame_message(selected, jpeg))
                await asyncio.wait_for(planner.entered.wait(), 1)
                successors = [picture(i) for i in (2, 3, 4)]
                for env, image in successors:
                    await ws.inject(frame_message(env, image))
                await asyncio.sleep(.02)
                planner.release.set()
                result = await wait_for(ws, lambda m: m["type"] == "tracking_result")
                self.assertEqual(planner.input["jpeg"], jpeg)
                self.assertEqual(planner.input["pcm"], ONE_SECOND)
                self.assertEqual(planner.input["envelope"]["frame_id"], selected["frame_id"])
                self.assertEqual(base64.b64decode(requests[0]["jpeg_b64"]), jpeg)
                self.assertEqual(requests[0]["clicks"], [{"x": 19.5, "y": 44.5, "obj_id": 1}])
                self.assertEqual([r["frame_id"] for r in requests],
                                 [selected["frame_id"]] + [e["frame_id"] for e, _ in successors])
                self.assertTrue(all(r["clicks"] == [] for r in requests[1:]))
                self.assertEqual(result["payload"]["frame_id"], successors[-1][0]["frame_id"])
                live, live_jpeg = picture(5)
                await ws.inject(frame_message(live, live_jpeg))
                await wait_for(ws, lambda m: m["type"] == "tracking_result"
                               and m["payload"]["frame_id"] == live["frame_id"])
                self.assertFalse(any(m["type"] == "scene_op" for m in ws.sent))
                await ws.inject(msg("cancel", {"turn_id": 1}, turn_id=1))
                await wait_for(ws, lambda m: m["type"] == "tracking_status" and m["payload"]["state"] == "stopped")
                count = len(requests)
                e, j = picture(6)
                await ws.inject(frame_message(e, j))
                await asyncio.sleep(.03)
                self.assertEqual(len(requests), count)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                for t in list(state.turn_tasks.values()):
                    t.cancel()
                await asyncio.gather(*list(state.turn_tasks.values()), return_exceptions=True)
                await bridge.stop()

    async def test_expired_history_never_seeds_latest_instead(self):
        history = FrameHistory(max_frames=2)
        selected = history.add(*picture(1))
        for i in range(2, 5):
            history.add(*picture(i))
        bridge = Sam2Bridge("unused", mock.AsyncMock(), history=history)
        bridge.epoch = selected.envelope["stage_epoch"]
        with self.assertRaisesRegex(TrackingError, "history expired"):
            await bridge.seed(selected, {"type": "image_point", "u": .5, "v": .5}, 0)
        self.assertIsNone(bridge.task)

    async def test_cancel_or_reset_suppresses_late_model_selection(self):
        planner, ws = DelayedPlanner(), DummyWs()
        state = CoordinatorState(planner)
        bridge = state.tracking = Sam2Bridge("unused", _turn_sender(ws, state))
        task = asyncio.create_task(handle_connection(ws, state))
        try:
            e, j = picture(1)
            await ws.inject(frame_message(e, j))
            await ws.inject(audio_chunk(ONE_SECOND))
            await ws.inject(msg("utterance_end", {"frame_id": e["frame_id"]}))
            await asyncio.wait_for(planner.entered.wait(), 1)
            await ws.inject(msg("cancel", {"turn_id": 1}, turn_id=1))
            await wait_for(ws, lambda m: m["type"] == "tracking_status" and m["payload"]["state"] == "stopped")
            planner.release.set()
            await asyncio.sleep(.03)
            self.assertIsNone(bridge.task)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await bridge.stop()

    async def test_wrong_frame_response_rejected_and_legacy_accepted(self):
        history = FrameHistory()
        frame = history.add(*picture())
        bridge = Sam2Bridge("unused", mock.AsyncMock())
        socket = mock.Mock(send=mock.AsyncMock(), recv=mock.AsyncMock())
        socket.recv.return_value = json.dumps({"type": "result", "objects": [], "frame_id": "wrong"})
        with self.assertRaisesRegex(TrackingError, "wrong frame"):
            await bridge._exchange(socket, frame, [])
        socket.recv.return_value = '{"type":"result","objects":[]}'
        self.assertEqual((await bridge._exchange(socket, frame, []))["objects"], [])

    async def test_frame_validation_and_bounded_storage(self):
        history = FrameHistory(max_frames=2)
        env, jpeg = picture()
        with self.assertRaises(TrackingError):
            history.add(env, b"not jpeg")
        wrong = dict(env, sent_w=100)
        with self.assertRaises(TrackingError):
            history.add(wrong, jpeg)
        history.add(env, jpeg)
        with self.assertRaises(TrackingError):
            history.add(env, jpeg)
        for i in range(2, 8):
            history.add(*picture(i))
        self.assertEqual(len(history.frames), 2)
        self.assertEqual(history.bytes, sum(len(f.jpeg) for f in history.frames))

    async def test_point_validation_and_edge_clamp(self):
        frame = FrameHistory().add(*picture())
        for value in (True, float("nan"), float("inf"), -1, 2, "0.5"):
            with self.assertRaises(TrackingError):
                point_click({"type": "image_point", "u": value, "v": .5}, frame)
        self.assertEqual(point_click({"type": "image_point", "u": 0, "v": 1}, frame),
                         {"x": 0, "y": 59, "obj_id": 1})

    async def test_tracking_planner_uses_audio_image_and_ignores_model_frame_id(self):
        planner = YibuPlanner(tracking=True, purpose="track-object")
        e, j = picture()
        reply = '{"heard":"track the laptop","track":{"type":"image_point","u":0.2,"v":0.3,"frame_id":"invented"}}'
        with mock.patch.object(planner, "_call", return_value=(reply, {"call_id": "audit"})) as call:
            result = await planner.plan(pcm=ONE_SECOND, jpeg=j, envelope=e, context=[])
        self.assertEqual(result.tracking_target, {"type": "image_point", "u": .2, "v": .3})
        self.assertEqual(result.ops, [])
        body = call.call_args.args[0]
        self.assertIn("image_url", str(body))
        self.assertIn("input_audio", str(body))
        self.assertEqual(result.audit_id, "audit")
        with mock.patch.object(planner, "_call", return_value=(reply, {})):
            missing = await planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=e, context=[])
        self.assertIsNone(missing.tracking_target)
        for text in ('{}', '{"track":null}', '{"track":{"type":"image_point","u":NaN,"v":0.5}}'):
            self.assertIsNone(parse_tracking_reply(text)[2])
