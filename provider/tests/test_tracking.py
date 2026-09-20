"""SAM2 highlight tool: bridge, highlight args, tool-call handling, schemas.

The model calls highlight_object(label, u, v); the server validates and
seeds SAM2 video tracking on the question frame. No GPU or cloud needed:
a fake SAM2 socket double and stub bridges cover the seams.
"""
import asyncio
import base64
import io
import json
import unittest
from unittest import mock

from PIL import Image
import websockets

from coordinator.live_turn import (
    HIGHLIGHT_DECL,
    handle_highlight_tool,
    parse_highlight_args,
)
from coordinator.sam2_bridge import FrameHistory, Sam2Bridge, TrackingError, point_click
from coordinator.server import _feed_tracking_frame, _note_tracking_epoch, _sendable
from protocol.validate import load_fixture
from protocol.ids import new_ulid
from voice.live_session import LiveSession


def picture(index=1, size=(80, 60)):
    out = io.BytesIO()
    Image.new("RGB", size, (index * 30 % 255, 100, 40)).save(out, format="JPEG")
    envelope = load_fixture("valid", "capture_envelope.json")
    envelope.update(frame_id=new_ulid(), t_unix_ns=index, sent_w=size[0], sent_h=size[1])
    return envelope, out.getvalue()


class FakeLive:
    def __init__(self):
        self.responses = []

    async def send_tool_response(self, call_id, name, result):
        self.responses.append((call_id, name, result))


class FakeTurn:
    def __init__(self, tombstoned=False):
        self.turn_id = 7
        self.utterance_id = "u7"
        self.tombstoned = tombstoned


class FakeBridge:
    def __init__(self, history=None, seed_error=None):
        self.history = history or FrameHistory()
        self.seed_error = seed_error
        self.begun = []
        self.seeded = []
        self.epoch = None
        self.task = None

    async def begin(self, turn_id, utterance_id):
        self.begun.append((turn_id, utterance_id))
        return 0

    async def seed(self, frame, target, generation):
        self.seeded.append((frame, target, generation))
        if self.seed_error is not None:
            raise self.seed_error

    async def stop(self):
        pass

    async def reset(self):
        pass

    async def status(self, state, text, **extra):
        pass


class FakeState:
    def __init__(self, live=None, turn=None, tracking=None, framed=None):
        self.live = live
        self._live_turn = turn
        self.tracking = tracking
        self._live_frame = framed


class HighlightArgsTests(unittest.TestCase):
    def test_fractions_accepted(self):
        target = parse_highlight_args({"label": "the red mug", "u": 0.25, "v": 0.75}, 640, 480)
        self.assertEqual(target, {"type": "image_point", "u": 0.25, "v": 0.75,
                                  "label": "the red mug"})

    def test_pixels_normalized_when_size_known(self):
        target = parse_highlight_args({"label": "x", "u": 492, "v": 351}, 640, 480)
        self.assertAlmostEqual(target["u"], 492 / 640)
        self.assertAlmostEqual(target["v"], 351 / 480)

    def test_mixed_frames_normalize_per_axis(self):
        target = parse_highlight_args({"label": "x", "u": 0.5, "v": 351}, 640, 480)
        self.assertEqual(target["u"], 0.5)
        self.assertAlmostEqual(target["v"], 351 / 480)

    def test_pixels_rejected_without_size(self):
        self.assertIsNone(parse_highlight_args({"label": "x", "u": 492, "v": 351}))

    def test_rejects_bad_args(self):
        good = {"label": "x", "u": 0.5, "v": 0.5}
        for broken in (
            None, [], "highlight",
            {**good, "label": ""}, {**good, "label": "   "}, {**good, "label": 3},
            {**good, "u": True}, {**good, "u": float("nan")},
            {**good, "u": float("inf")}, {**good, "u": "0.5"},
            {**good, "u": -0.1},
            {"label": "x", "u": 900, "v": 10},
            {"label": "x", "u": 0.5},
        ):
            self.assertIsNone(parse_highlight_args(broken, 640, 480), broken)

    def test_label_trimmed_and_capped(self):
        target = parse_highlight_args({"label": "  mug  ", "u": 0.1, "v": 0.1})
        self.assertEqual(target["label"], "mug")
        target = parse_highlight_args({"label": "y" * 100, "u": 0.1, "v": 0.1})
        self.assertEqual(len(target["label"]), 64)


class HighlightToolTests(unittest.IsolatedAsyncioTestCase):
    def make_state(self, **overrides):
        envelope, jpeg = picture()
        live, turn = FakeLive(), FakeTurn()
        bridge = FakeBridge()
        kwargs = dict(live=live, turn=turn, tracking=bridge, framed=(envelope, jpeg))
        kwargs.update(overrides)
        return FakeState(**kwargs), live, bridge

    async def test_success_seeds_and_answers_ok(self):
        state, live, bridge = self.make_state()
        await handle_highlight_tool(state, {"label": "mug", "u": 0.25, "v": 0.75}, "call-1")
        self.assertEqual(bridge.begun, [(7, "u7")])
        self.assertEqual(len(bridge.seeded), 1)
        frame, target, generation = bridge.seeded[0]
        self.assertEqual(target["u"], 0.25)
        self.assertEqual(live.responses,
                         [("call-1", "highlight_object", {"ok": True, "message": "Selecting mug…"})])

    async def test_preempted_mid_seed_stops_and_answers_interrupted(self):
        state, live, _ = self.make_state()
        turn = state._live_turn

        class PreemptingBridge(FakeBridge):
            async def seed(self, frame, target, generation):
                turn.tombstoned = True
                await super().seed(frame, target, generation)

        bridge = PreemptingBridge()
        state.tracking = bridge
        stopped = []
        bridge.stop = lambda: stopped.append(True) or asyncio.sleep(0)
        await handle_highlight_tool(state, {"label": "mug", "u": 0.5, "v": 0.5}, "c7")
        self.assertTrue(stopped)
        self.assertEqual(live.responses[0][2], {"ok": False, "message": "Interrupted."})

    async def test_honest_failures_answer_unseeded(self):
        state, live, _ = self.make_state(tracking=None)
        await handle_highlight_tool(state, {"label": "mug", "u": 0.5, "v": 0.5}, "c1")
        self.assertFalse(live.responses[0][2]["ok"])
        state, live, _ = self.make_state(turn=FakeTurn(tombstoned=True))
        await handle_highlight_tool(state, {"label": "mug", "u": 0.5, "v": 0.5}, "c2")
        self.assertFalse(live.responses[0][2]["ok"])
        state, live, _ = self.make_state(framed=None)
        await handle_highlight_tool(state, {"label": "mug", "u": 0.5, "v": 0.5}, "c3")
        self.assertFalse(live.responses[0][2]["ok"])
        state, live, bridge = self.make_state()
        await handle_highlight_tool(state, {"label": "", "u": 0.5, "v": 0.5}, "c4")
        self.assertFalse(live.responses[0][2]["ok"])
        self.assertEqual(bridge.seeded, [])

    async def test_seed_error_returns_model_message(self):
        state, live, _ = self.make_state()
        state.tracking = FakeBridge(seed_error=TrackingError("No unambiguous image point."))
        await handle_highlight_tool(state, {"label": "mug", "u": 0.5, "v": 0.5}, "c5")
        self.assertEqual(live.responses[0][2],
                         {"ok": False, "message": "No unambiguous image point."})

    async def test_no_live_session_still_safe(self):
        state, _, _ = self.make_state(live=None)
        await handle_highlight_tool(state, {"label": "mug", "u": 0.5, "v": 0.5}, "c6")


class ToolCallDispatchTests(unittest.TestCase):
    def test_tool_call_event_reaches_callback(self):
        seen = []
        session = LiveSession(on_tool_call=lambda n, a, c: seen.append((n, a, c)))
        session._dispatch({"toolCall": {"functionCalls": [
            {"name": "highlight_object", "args": {"label": "mug", "u": 0.1, "v": 0.2},
             "id": "call-9"}]}})
        self.assertEqual(seen, [("highlight_object", {"label": "mug", "u": 0.1, "v": 0.2},
                                 "call-9")])

    def test_malformed_tool_calls_ignored(self):
        seen = []
        session = LiveSession(on_tool_call=lambda n, a, c: seen.append((n, a, c)))
        session._dispatch({"toolCall": {"functionCalls": ["nope", None, {"name": "x"}]}})
        self.assertEqual(seen, [("x", {}, "")])
        session._dispatch({"toolCall": {}})
        session._dispatch({})
        self.assertEqual(len(seen), 1)

    def test_declaration_shape(self):
        self.assertEqual(HIGHLIGHT_DECL["name"], "highlight_object")
        required = HIGHLIGHT_DECL["parameters"]["required"]
        self.assertEqual(sorted(required), ["label", "u", "v"])


class BridgeTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_track_auto_stops_at_ttl(self):
        requests = []

        async def sam(ws):
            async for raw in ws:
                request = json.loads(raw)
                requests.append(request)
                await ws.send(json.dumps({"type": "result", "frame_id": request["frame_id"],
                                          "objects": [{"obj_id": 1, "mask_b64": "existing-mask"}]}))

        async with websockets.serve(sam, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            sent = []

            async def send(mtype, turn_id, payload, uid):
                sent.append((mtype, payload))

            bridge = Sam2Bridge(url, send, ttl=0.15)
            self.assertEqual(bridge.ttl, 0.15)
            envelope, jpeg = picture(1)
            bridge.epoch = envelope["stage_epoch"]
            frame = bridge.history.add(envelope, jpeg)
            generation = await bridge.begin(1, "u1")
            await bridge.seed(frame, {"type": "image_point", "u": .25, "v": .75}, generation)
            for _ in range(100):
                if any(t == "tracking_result" for t, _ in sent):
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(any(t == "tracking_result" for t, _ in sent))
            stopped = None
            for _ in range(100):
                stopped = next((p for t, p in sent
                                if t == "tracking_status" and p["state"] == "stopped"), None)
                if stopped is not None:
                    break
                await asyncio.sleep(0.02)
            self.assertIsNotNone(stopped)
            count = len(requests)
            await asyncio.sleep(0.1)
            self.assertEqual(len(requests), count)
            await bridge.stop()

    async def test_default_ttl_is_ten_seconds(self):
        bridge = Sam2Bridge("unused", mock.AsyncMock())
        self.assertEqual(bridge.ttl, 10.0)

    async def test_begin_starts_a_fresh_history(self):
        bridge = Sam2Bridge("unused", mock.AsyncMock())
        envelope, jpeg = picture(1)
        bridge.history.add(envelope, jpeg)
        await bridge.begin(1, "u1")
        self.assertEqual(len(bridge.history.frames), 0)
        frame = bridge.history.add(*picture(2))
        self.assertEqual(len(bridge.history.frames), 1)

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

    async def test_cancel_or_reset_suppresses_late_seed(self):
        history = FrameHistory()
        frame = history.add(*picture())
        bridge = Sam2Bridge("unused", mock.AsyncMock(), history=history)
        await bridge.begin(1, "u1")
        await bridge.reset()
        await bridge.seed(frame, {"type": "image_point", "u": .5, "v": .5}, 0)
        self.assertIsNone(bridge.task)

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

    async def test_tracking_frames_feed_and_stage_change_resets(self):
        envelope, jpeg = picture(1)
        bridge = FakeBridge()
        state = FakeState(tracking=bridge)
        message = {"payload": {"jpeg_b64": base64.b64encode(jpeg).decode(), "tracking": True},
                   "utterance_id": None}
        await _note_tracking_epoch(state, envelope)
        self.assertEqual(bridge.epoch, envelope["stage_epoch"])
        await _feed_tracking_frame(state, message, envelope)
        self.assertEqual(len(bridge.history.frames), 1)
        envelope2, jpeg2 = picture(2)
        envelope2["stage_epoch"] = envelope["stage_epoch"] + 1
        statuses = []
        bridge.status = lambda s, t, **e: statuses.append((s, t)) or asyncio.sleep(0)
        await _note_tracking_epoch(state, envelope2)
        stopped = [s for s in statuses if s[0] == "stopped"]
        self.assertTrue(stopped)


class TrackingSchemaTests(unittest.TestCase):
    def test_tracking_messages_validate(self) -> None:
        status = json.loads(_sendable("tracking_status", "s", 1,
                                      {"state": "selecting", "text": "x", "generation": 0}, "u"))
        self.assertEqual(status["type"], "tracking_status")
        result = json.loads(_sendable("tracking_result", "s", 1,
                                      {"frame_id": "f", "seed_frame_id": "f", "generation": 0,
                                       "stage_epoch": 1, "width": 80, "height": 60,
                                       "objects": [], "envelope": {}}, "u"))
        self.assertEqual(result["type"], "tracking_result")


if __name__ == "__main__":
    unittest.main()
