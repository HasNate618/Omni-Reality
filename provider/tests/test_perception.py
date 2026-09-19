"""Offline perception contracts; synthetic JPEG only, no provider or recorded media."""
import argparse
import asyncio
import base64
import copy
import json
import os
import unittest
from unittest.mock import patch

from coordinator import server, turn
from coordinator.planner import YibuPlanner
from coordinator.session import CoordinatorState, UtteranceBuffer
from tests.test_coordinator import make_hello
from yibu_audit import ApiKeyConfigurationError

# ffmpeg-generated red 16x12 square, not camera/user media.
JPEG = base64.b64decode('/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMgD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABMAAEBAAAAAAAAAAAAAAAAAAAABgEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAAMABADASIAAhEAAxEA/9oADAMBAAIRAxEAPwCLAE1/f//Z')
PCM = b'\x00\x01' * 9600


def envelope():
    from pathlib import Path
    fixture = Path(__file__).parents[1] / 'protocol/fixtures/valid/capture_envelope.json'
    env = json.loads(fixture.read_text())
    env.update(sent_w=16, sent_h=12)
    return env


def message(kind, payload, uid='u1'):
    return dict(v=1, type=kind, session_id=None, turn_id=0, utterance_id=uid, payload=payload)


class PerceptionPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_question_carries_current_jpeg_and_wav_without_tools_or_false_transcript(self):
        planner = server.make_planner('yibu', perception_qa=True)
        calls = []

        async def complete(messages, tools_enabled):
            calls.append((copy.deepcopy(messages), tools_enabled))
            return {'choices': [{'message': {'content': 'A red square.', 'tool_calls': [{'id': 'ignored'}]}}]}

        planner._complete_fn = complete
        result = await planner.plan(pcm=PCM, jpeg=JPEG, envelope=envelope(), context=[])
        self.assertEqual((result.ops, result.heard, result.text), ([], None, 'A red square.'))
        self.assertEqual(len(calls), 1)
        messages, enabled = calls[0]
        self.assertFalse(enabled)
        parts = messages[-1]['content']
        self.assertEqual([p['type'] for p in parts], ['text', 'image_url', 'input_audio'])
        self.assertEqual(base64.b64decode(parts[1]['image_url']['url'].split(',')[1]), JPEG)
        self.assertTrue(base64.b64decode(parts[2]['input_audio']['data'].split(',')[1]).startswith(b'RIFF'))
        self.assertEqual(planner.purpose, 'perception-qa-turn')
        self.assertLessEqual(planner.max_tokens, 128)

    async def test_missing_camera_never_reuses_previous_image_and_discloses_failure(self):
        planner = server.make_planner('yibu', perception_qa=True)
        calls = []

        async def complete(messages, _tools):
            calls.append(copy.deepcopy(messages))
            return {'choices': [{'message': {'content': 'Please try again.'}}]}

        planner._complete_fn = complete
        await planner.plan(pcm=PCM, jpeg=JPEG, envelope=envelope(), context=[])
        result = await planner.plan(pcm=PCM, jpeg=None, envelope=None, context=[{'heard': None, 'said': 'A red square.'}])
        self.assertEqual(len(calls), 1, 'missing camera must not spend an Omni call or invite invented vision')
        self.assertTrue(result.text.startswith("I couldn't get a camera image."))
        self.assertEqual(result.ops, [])

    async def test_voice_only_reply_is_not_fabricated_as_user_history(self):
        prompts = []

        async def complete(messages, _tools):
            prompts.append(messages[-1]['content'][0]['text'])
            return {'choices': [{'message': {'content': 'Unique assistant reply.'}}]}

        planner = YibuPlanner(voice_only=True, complete_fn=complete)
        result = await planner.plan(pcm=PCM, jpeg=None, envelope=None, context=[])
        self.assertIsNone(result.heard)
        await planner.plan(pcm=PCM, jpeg=None, envelope=None, context=[{'heard': result.heard, 'said': result.text}])
        self.assertNotIn('user: Unique assistant reply.', prompts[1])
        self.assertNotIn('user: None', prompts[1])

    async def test_empty_model_reply_gives_non_spatial_recovery_line(self):
        planner = server.make_planner('yibu', perception_qa=True)

        async def complete(_messages, _tools):
            return {'choices': [{'message': {'content': None}}]}

        planner._complete_fn = complete
        result = await planner.plan(pcm=PCM, jpeg=JPEG, envelope=envelope(), context=[])
        self.assertIn('try again', result.text.lower())
        self.assertNotIn('put', result.text)


class PerceptionImageTests(unittest.TestCase):
    def test_valid_synthetic_image_and_rejection_reasons(self):
        from voice.perception_image import validate_jpeg
        self.assertIsNone(validate_jpeg(JPEG, envelope()))
        for data, env, reason in [
            (None, None, 'missing_image'),
            (b'private image payload', envelope(), 'bad_jpeg'),
            (JPEG[:-2], envelope(), 'bad_jpeg'),
            (JPEG + b'x' * 65536, envelope(), 'image_over_cap'),
            (JPEG, None, 'missing_envelope'),
            (JPEG, dict(envelope(), sent_w=17), 'dimension_mismatch'),
        ]:
            with self.subTest(reason=reason):
                self.assertEqual(validate_jpeg(data, env), reason)


class PerceptionServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_live_turn import FakeLive
        self.state = CoordinatorState(planner=server.make_planner('yibu', perception_qa=True))
        self.state.live_factory = lambda **cb: FakeLive(self, **cb)
        self.sent = []
        owner = self

        class Socket:
            async def send(self, raw):
                owner.sent.append(json.loads(raw))

        self.ws = Socket()

    async def feed(self, kind, payload, uid='u1'):
        await server.handle_text(self.ws, self.state, json.dumps(message(kind, payload, uid)))

    async def audio(self, uid='u1', pcm=PCM):
        await self.feed('audio_chunk', {'audio': {'encoding': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1, 'data_b64': base64.b64encode(pcm).decode()}}, uid)

    async def frame(self, uid='u1', jpeg=JPEG):
        await self.feed('frame', {'envelope': envelope(), 'jpeg_b64': base64.b64encode(jpeg).decode()}, uid)

    def live(self):
        return self.state.live

    async def finish(self, uid='u1', complete=True):
        await self.feed('utterance_end', {}, uid)
        # The utterance handler schedules the turn; yield so it starts.
        for _ in range(100):
            if self.state._live_turn is not None:
                break
            await asyncio.sleep(0.01)
        # Emit the session-side completion the fake cannot produce alone.
        # Recovery/short paths never wait: completing them would fabricate
        # a speak_final after the honest recovery speak.
        if complete and self.live() is not None and self.state._live_turn is not None:
            self.live().emit('on_turn_end')
            for _ in range(200):
                if self.state._live_turn is None:
                    break
                await asyncio.sleep(0.01)

    async def test_hello_negotiates_capture_and_audio_then_image_then_end_runs_once(self):
        await server.handle_text(self.ws, self.state, json.dumps(make_hello()))
        self.assertIs(self.sent[0]['payload']['perception_qa'], True)
        for _ in range(100):
            if self.live() is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(self.live())
        await self.audio()
        await self.frame()
        await self.finish()
        kinds = [m['type'] for m in self.sent]
        self.assertEqual(kinds[0], 'hello_ok')
        self.assertIn('turn_started', kinds)
        self.assertIn('speak_final', kinds)
        self.assertEqual(len(self.live().image_turns), 1)
        self.assertEqual(self.live().image_turns[0][0], JPEG)
        self.assertNotIn('u1', self.state.utterances)
        # Late data/replayed end must not open a second session turn.
        await self.audio()
        await self.frame()
        await self.finish()
        self.assertEqual(len(self.live().image_turns), 1)
        self.assertNotIn('u1', self.state.utterances)

    async def test_bad_frame_and_validation_errors_do_not_log_payload(self):
        await server.handle_text(self.ws, self.state, json.dumps(make_hello()))
        for _ in range(100):
            if self.live() is not None:
                break
            await asyncio.sleep(0.01)
        with self.assertLogs(level='INFO') as logs:
            await self.audio()
            await self.frame(jpeg=b'PRIVATE_MEDIA')
            env = envelope()
            env['camera'] = 'PRIVATE_MEDIA'
            await self.feed('frame', {'envelope': env, 'jpeg_b64': 'PRIVATE_MEDIA'})
            await self.finish(complete=False)
        self.assertNotIn('PRIVATE_MEDIA', '\n'.join(logs.output))
        self.assertIn('jpeg_rejected', '\n'.join(logs.output))
        live = self.live()
        self.assertTrue(live is None or live.image_turns == [])
        self.assertTrue(self.sent[-1]['payload']['text'].startswith("I couldn't get a camera image."))

    async def test_duplicate_frame_cannot_replace_first_image(self):
        await self.audio()
        await self.frame()
        await self.frame(jpeg=b'bad')
        self.assertEqual(self.state.utterances['u1'].jpeg, JPEG)

    async def test_too_short_never_calls_session(self):
        await self.audio(pcm=b'\0' * 100)
        await self.frame()
        await self.finish(complete=False)
        live = self.live()
        self.assertTrue(live is None or live.image_turns == [])
        self.assertFalse([m for m in self.sent if m['type'] == 'turn_started'])

    async def test_connection_close_cancels_pending_turn_and_clears_media(self):
        await server.handle_text(self.ws, self.state, json.dumps(make_hello()))
        for _ in range(100):
            if self.live() is not None:
                break
            await asyncio.sleep(0.01)
        await self.audio()
        await self.frame()
        await self.feed('utterance_end', {})
        for _ in range(100):
            if self.state._live_turn is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(self.state._live_turn)
        self.state.utterances['unused'] = UtteranceBuffer(pcm=bytearray(PCM), jpeg=JPEG)

        class ClosedSocket:
            async def recv(self):
                raise ConnectionError('private transport details')

        live = self.live()
        self.assertIsNotNone(live)
        await server.handle_connection(ClosedSocket(), self.state)
        self.assertFalse(self.state.turn_tasks)
        self.assertFalse(self.state.utterances)
        self.assertFalse(self.state.context)
        self.assertIsNone(self.state.last_envelope)
        self.assertFalse(live.is_open)
        self.assertIsNone(self.state.live)


class PerceptionWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loopback_websocket_runs_live_session_turn(self):
        import websockets
        from tests.test_live_turn import FakeLive
        state = CoordinatorState(planner=server.make_planner('yibu', perception_qa=True))
        state.live_factory = lambda **cb: FakeLive(self, **cb)

        async def handler(ws):
            await server.handle_connection(ws, state)

        async with websockets.serve(handler, '127.0.0.1', 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(f'ws://127.0.0.1:{port}', proxy=None) as ws:
                await ws.send(json.dumps(make_hello()))
                hello = json.loads(await asyncio.wait_for(ws.recv(), 2))
                self.assertTrue(hello['payload']['perception_qa'])
                for kind, payload in [
                    ('audio_chunk', {'audio': {'encoding': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1, 'data_b64': base64.b64encode(PCM).decode()}}),
                    ('frame', {'envelope': envelope(), 'jpeg_b64': base64.b64encode(JPEG).decode()}),
                    ('utterance_end', {}),
                ]:
                    outgoing = message(kind, payload)
                    outgoing['session_id'] = hello['payload']['session_id']
                    await ws.send(json.dumps(outgoing))
                started = json.loads(await asyncio.wait_for(ws.recv(), 2))
                self.assertEqual(started['type'], 'turn_started')
                live = state.live
                self.assertEqual(len(live.image_turns), 1)
                self.assertEqual(live.image_turns[0][0], JPEG)
                # Session-side completion over the real socket: one chunk + final.
                live.emit('on_audio', b'\x11\x22' * 24000)
                live.emit('on_output_transcript', 'A red square.')
                live.emit('on_turn_end')
                chunk = json.loads(await asyncio.wait_for(ws.recv(), 5))
                final = json.loads(await asyncio.wait_for(ws.recv(), 5))
                self.assertEqual(chunk['type'], 'speak_chunk')
                self.assertEqual(chunk['payload']['seq'], 0)
                self.assertEqual(chunk['payload']['audio']['sample_rate'], 16000)
                self.assertEqual(final['type'], 'speak_final')
                self.assertEqual(final['payload']['text'], 'A red square.')
                self.assertEqual(final['payload']['voice_gate'], 'passed')

    async def test_voice_stub_twin_needs_no_session(self):
        from voice.test_tone import make_test_tone
        state = CoordinatorState(planner=server.make_planner('voice-stub', perception_qa=True))
        state.synthesizer = lambda _text: make_test_tone()
        sent = []

        class Socket:
            async def send(self, raw):
                sent.append(json.loads(raw))

        for kind, payload in [
            ('audio_chunk', {'audio': {'encoding': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1, 'data_b64': base64.b64encode(PCM).decode()}}),
            ('utterance_end', {}),
        ]:
            await server.handle_text(Socket(), state, json.dumps(message(kind, payload)))
        for _ in range(100):
            if any(m['type'] == 'speak' for m in sent):
                break
            await asyncio.sleep(0.01)
        speaks = [m for m in sent if m['type'] == 'speak']
        self.assertEqual(len(speaks), 1)
        self.assertEqual(base64.b64decode(speaks[0]['payload']['audio']['data_b64']), make_test_tone())
        self.assertIsNone(getattr(state, 'live', None))

    async def test_real_http_wrapper_audits_perception_without_payloads(self):
        import httpx
        import tempfile
        from pathlib import Path
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': 'PRIVATE_REPLY'}}]})

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'audit.jsonl'
            client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
            with patch.dict(os.environ, {'YIBU_API_KEY': 'offline-test-credential', 'YIBU_AUDIT_LOG': str(ledger)}), patch('yibu_http.httpx.Client', return_value=client):
                planner = server.make_planner('yibu', perception_qa=True)
                await planner.plan(pcm=PCM, jpeg=JPEG, envelope=envelope(), context=[])
            record_text = ledger.read_text()
            record = json.loads(record_text)
            self.assertEqual(record['purpose'], 'perception-qa-turn')
            self.assertTrue(record['ok'])
            self.assertIsNone(record['input_tokens'])
            for forbidden in ['PRIVATE_REPLY', 'offline-test-credential', 'input_audio', base64.b64encode(JPEG).decode()]:
                self.assertNotIn(forbidden, record_text)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['max_tokens'], 128)
            self.assertNotIn('tools', requests[0])
            self.assertNotIn('tool_choice', requests[0])


class PerceptionConfigTests(unittest.TestCase):
    def test_new_live_mode_missing_key_fails_before_bind(self):
        with patch.dict(os.environ, {'YIBU_API_KEY': ''}), patch('websockets.serve') as bind:
            with self.assertRaises(ApiKeyConfigurationError):
                asyncio.run(server.run_server(planner_kind='yibu', perception_qa=True))
            bind.assert_not_called()

    def test_modes_validate_and_stub_never_needs_key(self):
        parser = argparse.ArgumentParser()
        for planner, voice, expected in [('yibu', False, True), ('voice-stub', False, True), ('mark', False, False), ('yibu', True, False)]:
            args = argparse.Namespace(planner=planner, voice_only=voice, perception_qa=True)
            if expected:
                server._validate_cli_args(parser, args)
            else:
                with self.assertRaises(SystemExit):
                    server._validate_cli_args(parser, args)
        with patch.dict(os.environ, {'YIBU_API_KEY': ''}):
            self.assertTrue(server.make_planner('voice-stub', perception_qa=True).perception_qa)
