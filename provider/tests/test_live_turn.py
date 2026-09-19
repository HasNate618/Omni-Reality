"""Coordinator live-session turn contracts with a fake session. No network."""
import asyncio
import unittest

from coordinator.session import CoordinatorState, UtteranceBuffer
from voice.live_session import LiveSessionError

PCM_06S = b"\x00\x01" * int(0.6 * 16000)
import base64 as _b64
JPEG = _b64.b64decode('/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMgD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABMAAEBAAAAAAAAAAAAAAAAAAAABgEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAAMABADASIAAhEAAxEA/9oADAMBAAIRAxEAPwCLAE1/f//Z')  # real ffmpeg 16x12 red square


class FakeLive:
    def __init__(self, test, fail_connect=False, **callbacks):
        self.test = test
        self.sent_audio = []
        self.image_turns = []
        self.connects = 0
        self.fail_connect = fail_connect
        self.callbacks = dict(callbacks)
        self.is_open = True

    async def connect(self, timeout=20.0):
        self.connects += 1
        if self.fail_connect:
            raise LiveSessionError('down')

    async def send_audio(self, pcm):
        self.sent_audio.append(bytes(pcm))

    async def start_image_turn(self, jpeg, pcm, text):
        self.image_turns.append((bytes(jpeg), bytes(pcm), str(text)))

    async def close(self):
        self.is_open = False

    def emit(self, name, *args):
        self.callbacks[name](*args)


def make_state(test, fail_connect=False):
    state = CoordinatorState(planner=None)
    state.live_factory = lambda **cb: FakeLive(test, fail_connect=fail_connect, **cb)
    return state


class LiveTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_hello_warms_session_once(self):
        from coordinator import live_turn
        state = make_state(self)
        self.assertTrue(await live_turn.ensure_live_session(state))
        self.assertTrue(await live_turn.ensure_live_session(state))
        self.assertEqual(state.live.connects, 1)

    async def test_connect_failure_degrades_honestly(self):
        from coordinator import live_turn
        state = make_state(self, fail_connect=True)
        sent = []
        self.assertFalse(await live_turn.ensure_live_session(state))
        await live_turn.speak_recovery(state, lambda *a: sent.append(a) or asyncio.sleep(0), 1, 'u1')
        self.assertEqual(sent[0][0], 'speak')

    async def test_utterance_end_sends_image_turn(self):
        from coordinator import live_turn
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S), jpeg=JPEG,
                              envelope={'frame_id': 'f', 'stage_epoch': 0,
                                        'sent_w': 16, 'sent_h': 12})
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        task = asyncio.create_task(live_turn.start_live_turn(state, send, 'u1', buf))
        await asyncio.sleep(0)
        live = state.live
        live.emit('on_turn_end')
        await asyncio.wait_for(task, 5)
        self.assertEqual(len(live.image_turns), 1)
        self.assertEqual(live.image_turns[0][0], JPEG)
        self.assertEqual(live.image_turns[0][1], PCM_06S)
        self.assertIn('twenty-five', live.image_turns[0][2])
        self.assertEqual(sent[0][0], 'turn_started')

    async def test_missing_image_never_calls_session(self):
        from coordinator import live_turn
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S))
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        await live_turn.start_live_turn(state, send, 'u1', buf)
        self.assertEqual(state.live.image_turns, [])
        speaks = [p for t, p in sent if t == 'speak']
        self.assertEqual(len(speaks), 1)
        self.assertTrue(speaks[0]['text'].startswith("I couldn't get a camera image."))
        self.assertIsNone(speaks[0]['audio'])

    async def test_streamed_audio_becomes_ordered_chunks_then_final(self):
        from coordinator import live_turn
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S), jpeg=JPEG,
                              envelope={'frame_id': 'f', 'stage_epoch': 0,
                                        'sent_w': 16, 'sent_h': 12})
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        task = asyncio.create_task(live_turn.start_live_turn(state, send, 'u1', buf))
        await asyncio.sleep(0)
        live = state.live
        live.emit('on_audio', b'\x11\x22' * 24000)  # 1 s of 24 kHz
        live.emit('on_audio', b'\x33\x44' * 24000)
        live.emit('on_output_transcript', 'A square.')
        live.emit('on_turn_end')
        await asyncio.wait_for(task, 5)
        chunks = [p for t, p in sent if t == 'speak_chunk']
        finals = [p for t, p in sent if t == 'speak_final']
        self.assertEqual([c['seq'] for c in chunks], [0, 1])
        import base64
        self.assertEqual(len(base64.b64decode(chunks[0]['audio']['data_b64'])), 32000)
        self.assertEqual(chunks[0]['audio']['sample_rate'], 16000)
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]['text'], 'A square.')
        self.assertEqual(finals[0]['voice_gate'], 'passed')

    async def test_interruption_tombstones_and_drops_late_audio(self):
        from coordinator import live_turn
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S), jpeg=JPEG,
                              envelope={'frame_id': 'f', 'stage_epoch': 0,
                                        'sent_w': 16, 'sent_h': 12})
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        task = asyncio.create_task(live_turn.start_live_turn(state, send, 'u1', buf))
        await asyncio.sleep(0)
        live = state.live
        live.emit('on_interrupted')
        live.emit('on_audio', b'\x11\x22' * 24000)
        live.emit('on_turn_end')
        await asyncio.wait_for(task, 5)
        kinds = [t for t, _ in sent]
        self.assertIn('stop_speak', kinds)
        self.assertNotIn('speak_chunk', kinds)
        self.assertNotIn('speak_final', kinds)

    async def test_silent_session_times_out_and_recycles(self):
        from coordinator import live_turn
        from unittest.mock import patch
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S), jpeg=JPEG,
                              envelope={'frame_id': 'f', 'stage_epoch': 0,
                                        'sent_w': 16, 'sent_h': 12})
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        with patch.object(live_turn, 'LIVE_TURN_TIMEOUT_S', 0.05):
            turn_id = await live_turn.start_live_turn(state, send, 'u1', buf)
        self.assertEqual(turn_id, 1)
        speaks = [p for t, p in sent if t == 'speak']
        self.assertEqual(len(speaks), 1)
        self.assertIn("couldn't reach", speaks[0]['text'])
        self.assertIsNone(state.live)
        self.assertIsNone(state._live_turn)

    async def test_new_utterance_preempts_stale_turn(self):
        from coordinator import live_turn
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S), jpeg=JPEG,
                              envelope={'frame_id': 'f', 'stage_epoch': 0,
                                        'sent_w': 16, 'sent_h': 12})
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        first = asyncio.create_task(live_turn.start_live_turn(state, send, 'u1', buf))
        await asyncio.sleep(0)
        second = asyncio.create_task(live_turn.start_live_turn(state, send, 'u2', buf))
        first_id = await asyncio.wait_for(first, 5)
        self.assertEqual(first_id, 1)
        live = state.live
        live.emit('on_audio', b'\x11\x22' * 24000)
        live.emit('on_turn_end')
        await asyncio.wait_for(second, 5)
        tagged = [(t, p.get('turn_id')) for t, p in sent]
        self.assertIn(('stop_speak', 1), tagged)
        self.assertNotIn(('speak_final', 1), tagged)
        self.assertIn(('speak_final', 2), tagged)

    async def test_usage_deltas_audited_per_turn(self):
        from coordinator import live_turn
        records = []
        state = make_state(self)
        await live_turn.ensure_live_session(state)
        buf = UtteranceBuffer(pcm=bytearray(PCM_06S), jpeg=JPEG,
                              envelope={'frame_id': 'f', 'stage_epoch': 0,
                                        'sent_w': 16, 'sent_h': 12})
        sent = []
        async def send(mtype, turn_id, payload, uid):
            sent.append((mtype, payload))
        live_turn.append_audit_record = lambda **kw: records.append(kw) or {}
        try:
            state._live_last_totals = {'totalTokenCount': 100}  # prior turn consumed
            task = asyncio.create_task(live_turn.start_live_turn(state, send, 'u1', buf))
            await asyncio.sleep(0)
            live = state.live
            live.emit('on_usage', {'totalTokenCount': 169})
            live.emit('on_usage', {'totalTokenCount': 213})
            live.emit('on_turn_end')
            await asyncio.wait_for(task, 5)
        finally:
            del live_turn.append_audit_record
        turns = [r for r in records if r.get('purpose') == 'rt-voice-turn']
        self.assertEqual(len(turns), 1)
        # Final cumulative minus turn-start baseline: this turn owns 113, not 213.
        self.assertEqual(turns[0]['response_json']['usage_delta'], {'totalTokenCount': 113})
