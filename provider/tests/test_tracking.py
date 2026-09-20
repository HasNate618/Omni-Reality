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

from coordinator.planner import (
    MAX_TRACKED_OBJECTS,
    PlanResult,
    YibuPlanner,
    parse_tracking_reply,
)
from coordinator.sam2_bridge import FrameHistory, Sam2Bridge, TrackingError, point_click
from coordinator.server import CoordinatorState, _turn_sender, handle_connection
from coordinator.turn import SAY_TRACKING_MANY
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


LAPTOP = {"type": "image_point", "u": .25, "v": .75, "label": "laptop"}
MUG = {"type": "image_point", "u": .5, "v": .5, "label": "mug"}


class DelayedPlanner:
    def __init__(self, targets=(LAPTOP,)):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.input = None
        self.targets = list(targets)

    async def plan(self, **kwargs):
        self.input = kwargs
        self.entered.set()
        await self.release.wait()
        return PlanResult([], "", tracking_targets=list(self.targets))


def sam_double(requests, *, drop_after_first=False):
    """Stand-in for sam2_ws_server: remember which obj_ids were clicked and
    return a mask per tracked object on every later frame, as the real server
    does. `drop_after_first` never starts anything past obj_id 1."""
    tracked: list[int] = []

    async def handler(ws):
        async for raw in ws:
            request = json.loads(raw)
            requests.append(request)
            for click in request.get("clicks") or []:
                if click["obj_id"] not in tracked:
                    tracked.append(click["obj_id"])
            started = tracked[:1] if drop_after_first else tracked
            await ws.send(json.dumps({
                "type": "result", "frame_id": request["frame_id"],
                "objects": [{"obj_id": i, "mask_b64": f"mask-{i}"} for i in started],
            }))

    return handler


class TrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_frame_before_or_after_end_then_replay_and_live(self):
        requests = []

        async with websockets.serve(sam_double(requests), "127.0.0.1", 0) as server:
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
                first = await wait_for(ws, lambda m: m["type"] == "tracking_result")
                # The seed mask is published before the backlog is replayed, so
                # the wearer sees it as soon as the point lands rather than one
                # catch-up later.
                self.assertEqual(first["payload"]["frame_id"], selected["frame_id"])
                self.assertEqual(first["payload"]["seed_frame_id"], selected["frame_id"])
                result = await wait_for(ws, lambda m: m["type"] == "tracking_result"
                                        and m["payload"]["frame_id"] == successors[-1][0]["frame_id"])
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

    async def test_two_objects_seed_together_on_the_one_selected_frame(self):
        requests = []

        async with websockets.serve(sam_double(requests), "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            planner, ws = DelayedPlanner([LAPTOP, MUG]), DummyWs()
            state = CoordinatorState(planner)
            bridge = state.tracking = Sam2Bridge(url, _turn_sender(ws, state))
            task = asyncio.create_task(handle_connection(ws, state))
            try:
                await ws.inject(make_hello())
                await wait_for(ws, lambda m: m["type"] == "hello_ok")
                selected, jpeg = picture(1)
                await ws.inject(frame_message(selected, jpeg))
                await ws.inject(audio_chunk(ONE_SECOND))
                await ws.inject(msg("utterance_end", {"frame_id": selected["frame_id"]}))
                await asyncio.wait_for(planner.entered.wait(), 1)
                planner.release.set()
                result = await wait_for(ws, lambda m: m["type"] == "tracking_result")
                # Both points are clicked on the same frame, which is what lets
                # SAM 2 open one session holding both objects.
                self.assertEqual(requests[0]["clicks"], [
                    {"x": 19.5, "y": 44.5, "obj_id": 1},
                    {"x": 39.5, "y": 29.5, "obj_id": 2},
                ])
                objects = result["payload"]["objects"]
                self.assertEqual([o["obj_id"] for o in objects], [1, 2])
                # The model's names ride along so the headset can label a mask.
                self.assertEqual([o["label"] for o in objects], ["laptop", "mug"])

                follow, follow_jpeg = picture(2)
                await ws.inject(frame_message(follow, follow_jpeg))
                later = await wait_for(ws, lambda m: m["type"] == "tracking_result"
                                       and m["payload"]["frame_id"] == follow["frame_id"])
                # Tracking continues for both without re-clicking either.
                self.assertEqual([o["obj_id"] for o in later["payload"]["objects"]], [1, 2])
                self.assertTrue(all(r["clicks"] == [] for r in requests[1:]))
                # Plural copy: the wearer asked for more than one thing.
                spoken = await wait_for(ws, lambda m: m["type"] == "speak")
                self.assertEqual(spoken["payload"]["text"], SAY_TRACKING_MANY)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                for t in list(state.turn_tasks.values()):
                    t.cancel()
                await asyncio.gather(*list(state.turn_tasks.values()), return_exceptions=True)
                await bridge.stop()

    async def test_seed_fails_when_sam2_starts_only_some_objects(self):
        """A half-started selection is an error, not a silently smaller set."""
        history = FrameHistory()
        frame = history.add(*picture())
        bridge = Sam2Bridge("unused", mock.AsyncMock(), history=history)
        socket = mock.Mock(send=mock.AsyncMock(), recv=mock.AsyncMock())
        socket.recv.return_value = json.dumps({
            "type": "result", "frame_id": frame.id,
            "objects": [{"obj_id": 1, "mask_b64": "mask-1"}],
        })
        clicks = [{"x": 1, "y": 1, "obj_id": 1}, {"x": 2, "y": 2, "obj_id": 2}]
        with self.assertRaisesRegex(TrackingError, "did not initialize 1 of the 2"):
            await bridge._exchange(socket, frame, clicks)
        # All of them back: accepted.
        socket.recv.return_value = json.dumps({
            "type": "result", "frame_id": frame.id,
            "objects": [{"obj_id": 1, "mask_b64": "mask-1"}, {"obj_id": 2, "mask_b64": "mask-2"}],
        })
        self.assertEqual(len((await bridge._exchange(socket, frame, clicks))["objects"]), 2)

    async def test_expired_history_never_seeds_latest_instead(self):
        history = FrameHistory(max_frames=2)
        selected = history.add(*picture(1))
        for i in range(2, 5):
            history.add(*picture(i))
        bridge = Sam2Bridge("unused", mock.AsyncMock(), history=history)
        bridge.epoch = selected.envelope["stage_epoch"]
        with self.assertRaisesRegex(TrackingError, "history expired"):
            await bridge.seed(selected, [{"type": "image_point", "u": .5, "v": .5}], 0)
        self.assertIsNone(bridge.task)
        with self.assertRaisesRegex(TrackingError, "No object was selected"):
            await bridge.seed(selected, [], 0)
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
        # Each selected object gets its own obj_id on the same seed frame.
        self.assertEqual(point_click({"type": "image_point", "u": 1, "v": 0}, frame, 3),
                         {"x": 79, "y": 0, "obj_id": 3})

    async def test_tracking_planner_uses_audio_image_and_ignores_model_frame_id(self):
        planner = YibuPlanner(tracking=True, purpose="track-object")
        e, j = picture()
        reply = '{"heard":"track the laptop","track":{"type":"image_point","u":0.2,"v":0.3,"frame_id":"invented"}}'
        with mock.patch.object(planner, "_call", return_value=(reply, {"call_id": "audit"})) as call:
            result = await planner.plan(pcm=ONE_SECOND, jpeg=j, envelope=e, context=[])
        self.assertEqual(result.tracking_targets,
                         [{"type": "image_point", "u": .2, "v": .3, "label": None}])
        self.assertEqual(result.ops, [])
        body = call.call_args.args[0]
        self.assertIn("image_url", str(body))
        self.assertIn("input_audio", str(body))
        self.assertEqual(result.audit_id, "audit")
        with mock.patch.object(planner, "_call", return_value=(reply, {})):
            missing = await planner.plan(pcm=ONE_SECOND, jpeg=None, envelope=e, context=[])
        self.assertEqual(missing.tracking_targets, [])
        for text in ('{}', '{"track":null}', '{"track":[]}',
                     '{"track":{"type":"image_point","u":NaN,"v":0.5}}'):
            self.assertEqual(parse_tracking_reply(text)[2], [])

    def test_pixel_coordinates_are_normalised(self) -> None:
        """The model answers in pixels despite the prompt; don't drop the target."""
        pixels = '{"heard":"track it","say":"","track":{"type":"image_point","u":492,"v":351}}'
        self.assertEqual(
            parse_tracking_reply(pixels, 640, 480)[2],
            [{"type": "image_point", "u": 492 / 640, "v": 351 / 480, "label": None}],
        )
        # Fractions still win when both readings are possible.
        fraction = '{"track":{"type":"image_point","u":0.5,"v":0.25}}'
        self.assertEqual(
            parse_tracking_reply(fraction, 640, 480)[2],
            [{"type": "image_point", "u": 0.5, "v": 0.25, "label": None}],
        )
        # Pixels inside a multi-object list are normalised the same way.
        listed = '{"track":[{"type":"image_point","u":492,"v":351},{"type":"image_point","u":64,"v":48}]}'
        self.assertEqual([(t["u"], t["v"]) for t in parse_tracking_reply(listed, 640, 480)[2]],
                         [(492 / 640, 351 / 480), (0.1, 0.1)])
        # Outside the image, or no size to work with: no target.
        self.assertEqual(parse_tracking_reply(pixels)[2], [])
        self.assertEqual(
            parse_tracking_reply('{"track":{"type":"image_point","u":900,"v":10}}', 640, 480)[2], [])

    def test_multiple_points_parsed_deduped_and_capped(self) -> None:
        reply = ('{"heard":"track the laptop and the mug","say":"On it.","track":['
                 '{"label":"laptop","type":"image_point","u":0.2,"v":0.3},'
                 '{"label":"mug","type":"image_point","u":0.8,"v":0.6}]}')
        say, heard, targets = parse_tracking_reply(reply)
        self.assertEqual((say, heard), ("On it.", "track the laptop and the mug"))
        self.assertEqual(targets, [
            {"type": "image_point", "u": 0.2, "v": 0.3, "label": "laptop"},
            {"type": "image_point", "u": 0.8, "v": 0.6, "label": "mug"},
        ])
        # One unusable entry never costs the objects listed beside it.
        mixed = ('{"track":[{"type":"image_point","u":0.2,"v":0.3},'
                 '{"type":"image_box","u":0.5,"v":0.5},'
                 '{"type":"image_point","u":NaN,"v":0.5},'
                 '{"type":"image_point","u":0.9,"v":0.9}]}')
        self.assertEqual([(t["u"], t["v"]) for t in parse_tracking_reply(mixed)[2]],
                         [(0.2, 0.3), (0.9, 0.9)])
        # The same object named twice takes one tracker slot, not two.
        duplicate = ('{"track":[{"type":"image_point","u":0.50,"v":0.50},'
                     '{"type":"image_point","u":0.52,"v":0.51},'
                     '{"type":"image_point","u":0.90,"v":0.10}]}')
        self.assertEqual([(t["u"], t["v"]) for t in parse_tracking_reply(duplicate)[2]],
                         [(0.5, 0.5), (0.9, 0.1)])
        # Never more trackers than SAM 2's per-frame budget allows.
        many = '{"track":[%s]}' % ",".join(
            '{"type":"image_point","u":%.2f,"v":0.5}' % (0.1 * i) for i in range(1, 8))
        self.assertEqual(len(parse_tracking_reply(many)[2]), MAX_TRACKED_OBJECTS)
        # The older single-object shape still parses, top-level label included.
        self.assertEqual(
            parse_tracking_reply('{"label":"mug","track":{"type":"image_point","u":0.4,"v":0.4}}')[2],
            [{"type": "image_point", "u": 0.4, "v": 0.4, "label": "mug"}])
