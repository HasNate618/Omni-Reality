"""A/B mode routing: push-to-talk turns vs live-conversation turns.

One coordinator session serves both. The route is chosen per utterance from
`payload.mode`, not from a startup flag, so pressing B on the headset must not
disturb the A-button tracking path and vice versa.
"""
import asyncio
import unittest
from unittest import mock

from coordinator import live_turn
from coordinator.server import CoordinatorState, _handle_utterance_end
from coordinator.session import UtteranceBuffer
from tests.test_turn import ONE_SECOND, UTT, msg


class Planner:
    """Plain double: mock.Mock() would make every getattr truthy."""
    perception_qa = False
    voice_only = False


def state_with_audio(pcm=ONE_SECOND):
    state = CoordinatorState(planner=Planner())
    buf = state.utterances.setdefault(UTT, UtteranceBuffer())
    buf.pcm.extend(pcm)
    return state


def utterance_end(payload):
    return msg("utterance_end", payload)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def route(self, payload):
        """Return ('ptt'|'live') by seeing which starter the server calls."""
        state = state_with_audio()
        with mock.patch("coordinator.server.start_turn") as ptt, \
             mock.patch("coordinator.server._start_live_utterance") as live:
            await _handle_utterance_end(mock.Mock(), state, utterance_end(payload))
        self.assertFalse(ptt.called and live.called, "exactly one path must run")
        if live.called:
            return "live"
        return "ptt" if ptt.called else "none"

    async def test_mode_live_runs_the_conversation_turn(self):
        self.assertEqual(await self.route({"utterance_id": UTT, "mode": "live"}), "live")

    async def test_mode_ptt_runs_the_tracking_turn(self):
        self.assertEqual(await self.route({"utterance_id": UTT, "mode": "ptt"}), "ptt")

    async def test_missing_mode_defaults_to_push_to_talk(self):
        # Older headset builds send no mode; they mean A-button push-to-talk.
        self.assertEqual(await self.route({"utterance_id": UTT}), "ptt")

    async def test_unknown_mode_defaults_to_push_to_talk(self):
        self.assertEqual(await self.route({"utterance_id": UTT, "mode": "nonsense"}), "ptt")

    async def test_ptt_records_the_selected_snapshot(self):
        state = state_with_audio()
        state.tracking = mock.Mock()
        with mock.patch("coordinator.server.start_turn"):
            await _handle_utterance_end(
                mock.Mock(), state,
                utterance_end({"utterance_id": UTT, "mode": "ptt", "frame_id": "frame-7"}))
        self.assertEqual(state.utterances[UTT].selected_frame_id, "frame-7")


class FramePinningTests(unittest.IsolatedAsyncioTestCase):
    """A B-mode snapshot must reach the utterance buffer, not only the tracker."""

    def setUp(self):
        import base64, io as _io
        from PIL import Image
        from protocol.validate import load_fixture
        from protocol.ids import new_ulid
        out = _io.BytesIO()
        Image.new("RGB", (640, 480), (90, 110, 130)).save(out, format="JPEG")
        self.jpeg = out.getvalue()
        env = load_fixture("valid", "capture_envelope.json")
        env.update(frame_id=new_ulid(), sent_w=640, sent_h=480)
        self.env = env
        self.msg = msg("frame", {"envelope": env,
                                 "utterance_id": UTT,
                                 "jpeg_b64": base64.b64encode(self.jpeg).decode()}, UTT)

    async def state_after_frame(self, tracking):
        from coordinator.server import _handle_frame
        state = CoordinatorState(planner=Planner())
        state.latest_stage_epoch = 0
        if tracking:
            bridge = mock.AsyncMock()
            bridge.epoch = self.env["stage_epoch"]
            bridge.history = mock.Mock()
            state.tracking = bridge
        await _handle_frame(mock.Mock(), state, self.msg)
        return state

    async def test_snapshot_pinned_while_tracking_is_enabled(self):
        state = await self.state_after_frame(tracking=True)
        buf = state.utterances.get(UTT)
        self.assertIsNotNone(buf, "frame must open/keep the utterance buffer")
        self.assertEqual(buf.jpeg, self.jpeg)
        # and it still reaches the tracker
        state.tracking.history.add.assert_called_once()

    async def test_snapshot_pinned_without_tracking(self):
        state = await self.state_after_frame(tracking=False)
        self.assertEqual(state.utterances[UTT].jpeg, self.jpeg)

    async def test_streamed_frame_without_utterance_stays_with_the_tracker(self):
        from coordinator.server import _handle_frame
        import base64
        state = CoordinatorState(planner=Planner())
        bridge = mock.AsyncMock()
        bridge.epoch = self.env["stage_epoch"]
        bridge.history = mock.Mock()
        state.tracking = bridge
        streamed = msg("frame", {"envelope": self.env,
                                 "jpeg_b64": base64.b64encode(self.jpeg).decode()}, None)
        await _handle_frame(mock.Mock(), state, streamed)
        self.assertEqual(state.utterances, {}, "A-mode stream must not open utterances")
        bridge.history.add.assert_called_once()


class TrackingIntentTests(unittest.TestCase):
    def test_tracking_phrases_trigger(self):
        for said in ["Track the laptop", "can you HIGHLIGHT the mug please",
                     "what's that on the shelf", "follow that bottle"]:
            self.assertTrue(live_turn.wants_tracking(said), said)

    def test_ordinary_conversation_does_not_trigger(self):
        for said in ["what colour is it", "tell me a joke", "how far away is the wall",
                     "", "thanks, that's great"]:
            self.assertFalse(live_turn.wants_tracking(said), said)


class LiveSeedTests(unittest.IsolatedAsyncioTestCase):
    def make_turn(self, state):
        turn = live_turn._Turn(1, UTT, mock.AsyncMock())
        turn.jpeg = b"jpeg-bytes"
        turn.envelope = {"frame_id": "frame-1"}
        turn.pcm = b"\x00\x00"
        state._live_turn = turn
        return turn

    async def test_transcript_starts_one_seed(self):
        state = CoordinatorState(planner=Planner())
        state.tracking = mock.Mock()
        turn = self.make_turn(state)
        with mock.patch.object(live_turn, "_seed_tracking",
                               new=mock.AsyncMock()) as seed:
            live_turn._on_heard(state, "track the laptop")
            live_turn._on_heard(state, " and keep tracking it")
            await asyncio.sleep(0)
        self.assertTrue(turn.seed_started)
        self.assertEqual(seed.await_count, 1, "a second phrase must not re-seed")

    async def test_no_seed_without_a_tracking_bridge(self):
        state = CoordinatorState(planner=Planner())
        state.tracking = None
        turn = self.make_turn(state)
        with mock.patch.object(live_turn, "_seed_tracking", new=mock.AsyncMock()) as seed:
            live_turn._on_heard(state, "track the laptop")
            await asyncio.sleep(0)
        self.assertFalse(turn.seed_started)
        seed.assert_not_awaited()

    async def test_plain_conversation_does_not_seed(self):
        state = CoordinatorState(planner=Planner())
        state.tracking = mock.Mock()
        self.make_turn(state)
        with mock.patch.object(live_turn, "_seed_tracking", new=mock.AsyncMock()) as seed:
            live_turn._on_heard(state, "what colour is the wall")
            await asyncio.sleep(0)
        seed.assert_not_awaited()

    async def test_seed_uses_this_utterance_frame_and_audio(self):
        state = CoordinatorState(planner=Planner())
        bridge = mock.AsyncMock()
        bridge.begin.return_value = 3
        bridge.generation = 3
        frame = object()
        bridge.history.wait_for = mock.AsyncMock(return_value=frame)
        state.tracking = bridge
        turn = self.make_turn(state)

        planner = mock.AsyncMock()
        planner.plan.return_value = mock.Mock(
            tracking_target={"type": "image_point", "u": 0.4, "v": 0.6})
        with mock.patch("coordinator.planner.YibuPlanner", return_value=planner):
            await live_turn._seed_tracking(state, turn)

        self.assertEqual(planner.plan.await_args.kwargs["jpeg"], b"jpeg-bytes")
        self.assertEqual(planner.plan.await_args.kwargs["pcm"], b"\x00\x00")
        bridge.history.wait_for.assert_awaited_once_with("frame-1")
        bridge.seed.assert_awaited_once_with(
            frame, {"type": "image_point", "u": 0.4, "v": 0.6}, 3)

    async def test_stale_generation_never_seeds(self):
        state = CoordinatorState(planner=Planner())
        bridge = mock.AsyncMock()
        bridge.begin.return_value = 3
        bridge.generation = 4          # a newer utterance already superseded us
        state.tracking = bridge
        turn = self.make_turn(state)
        planner = mock.AsyncMock()
        planner.plan.return_value = mock.Mock(tracking_target={"u": 0.1, "v": 0.1})
        with mock.patch("coordinator.planner.YibuPlanner", return_value=planner):
            await live_turn._seed_tracking(state, turn)
        bridge.seed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
