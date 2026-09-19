"""LiveSession contracts against an in-process fake socket. No network, no credit."""
import asyncio
import base64
import json
import unittest


JPEG = (b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 64 + b'\xff\xd9')


class FakeWs:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def send(self, raw):
        if self.closed:
            raise ConnectionError('closed')
        self.sent.append(raw)

    async def recv(self):
        item = await self.incoming.get()
        if item is None:
            raise ConnectionError('closed')
        return item

    async def close(self):
        self.closed = True
        self.incoming.put_nowait(None)


async def _ready(ws):
    return ws


def make_session(test, **overrides):
    from voice.live_session import LiveSession
    calls = []
    params = dict(
        api_key='offline-test-credential',
        on_audio=lambda b: calls.append(('audio', len(b))),
        on_input_transcript=lambda t: calls.append(('heard', t)),
        on_output_transcript=lambda t: calls.append(('said', t)),
        on_interrupted=lambda: calls.append(('interrupted', None)),
        on_usage=lambda u: calls.append(('usage', dict(u))),
        connector=lambda: _ready(test.fake),
    )
    params.update(overrides)
    return LiveSession(**params), calls


class LiveSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeWs()

    async def test_connect_sends_setup_and_waits_for_setup_complete(self):
        session, _ = make_session(self)
        task = asyncio.create_task(session.connect())
        await asyncio.sleep(0)
        frame = json.loads(self.fake.sent[0])
        self.assertEqual(frame['setup']['model'], 'models/gemini-3.1-flash-live-preview')
        self.assertEqual(frame['setup']['generationConfig']['responseModalities'], ['AUDIO'])
        self.fake.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.wait_for(task, 2)
        self.assertTrue(session.is_open)
        await session.close()

    async def test_setup_reject_raises_without_opening(self):
        from voice.live_session import LiveSessionError
        session, _ = make_session(self)
        task = asyncio.create_task(session.connect())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.fake.incoming.put_nowait(json.dumps({'error': {'code': 400}}))
        with self.assertRaises(LiveSessionError):
            await asyncio.wait_for(task, 2)
        self.assertFalse(session.is_open)

    async def test_audio_frames_carry_16k_pcm_and_drop_after_close(self):
        session, _ = make_session(self)
        self.fake.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.wait_for(session.connect(), 2)
        await session.send_audio(b'\x00\x01' * 800)
        chunk = json.loads(self.fake.sent[-1])['realtimeInput']['mediaChunks'][0]
        self.assertEqual(chunk['mimeType'], 'audio/pcm;rate=16000')
        self.assertEqual(base64.b64decode(chunk['data']), b'\x00\x01' * 800)
        await session.close()
        await session.send_audio(b'\x00\x01' * 800)  # must not raise
        self.assertEqual(len(self.fake.sent), 2)

    async def test_image_turn_shape(self):
        session, _ = make_session(self)
        self.fake.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.wait_for(session.connect(), 2)
        await session.start_image_turn(JPEG, b'\x00\x01' * 800, 'Answer in one sentence.')
        frame = json.loads(self.fake.sent[-1])
        self.assertTrue(frame['clientContent']['turnComplete'])
        parts = frame['clientContent']['turns'][0]['parts']
        self.assertEqual(parts[0]['text'], 'Answer in one sentence.')
        self.assertEqual(parts[1]['inlineData']['mimeType'], 'image/jpeg')
        self.assertEqual(base64.b64decode(parts[1]['inlineData']['data']), JPEG)
        self.assertEqual(parts[2]['inlineData']['mimeType'], 'audio/pcm;rate=16000')
        self.assertEqual(base64.b64decode(parts[2]['inlineData']['data']), b'\x00\x01' * 800)
        await session.close()

    async def test_receive_dispatches_audio_transcripts_interruption_usage(self):
        session, calls = make_session(self)
        self.fake.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.wait_for(session.connect(), 2)
        pcm = base64.b64encode(b'\x01\x02' * 480).decode()
        self.fake.incoming.put_nowait(json.dumps({'serverContent': {
            'inputTranscription': {'text': 'What is it?'},
            'modelTurn': {'parts': [{'inlineData': {'mimeType': 'audio/pcm;rate=24000', 'data': pcm}}]},
            'outputTranscription': {'text': 'A square.'}}}))
        self.fake.incoming.put_nowait(json.dumps({'serverContent': {'interrupted': True}}))
        self.fake.incoming.put_nowait(json.dumps({'usageMetadata': {'totalTokenCount': 169}}))
        for _ in range(12):
            if len(calls) >= 5:
                break
            await asyncio.sleep(0.01)
        kinds = [kind for kind, _ in calls]
        for expected in ('heard', 'audio', 'said', 'interrupted', 'usage'):
            self.assertIn(expected, kinds)
        self.assertEqual(calls[kinds.index('audio')][1], 960)
        await session.close()

    async def test_turn_complete_dispatches_end_of_turn(self):
        session, calls = make_session(self, on_turn_end=lambda: calls.append(('end', None)))
        self.fake.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.wait_for(session.connect(), 2)
        self.fake.incoming.put_nowait(json.dumps({'serverContent': {'turnComplete': True}}))
        for _ in range(20):
            if ('end', None) in calls:
                break
            await asyncio.sleep(0.01)
        self.assertIn(('end', None), calls)
        await session.close()

    async def test_callback_failure_never_kills_receive_loop(self):
        def boom(data):
            raise RuntimeError('must not escape')
        session, calls = make_session(self, on_audio=boom,
                                      on_output_transcript=lambda t: calls.append(('said', t)))
        self.fake.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.wait_for(session.connect(), 2)
        pcm = base64.b64encode(b'\x01\x02').decode()
        self.fake.incoming.put_nowait(json.dumps({'serverContent': {
            'modelTurn': {'parts': [{'inlineData': {'mimeType': 'audio/pcm', 'data': pcm}}]},
            'outputTranscription': {'text': 'Still here.'}}}))
        for _ in range(20):
            if ('said', 'Still here.') in calls:
                break
            await asyncio.sleep(0.01)
        self.assertIn(('said', 'Still here.'), calls)
        await session.close()
