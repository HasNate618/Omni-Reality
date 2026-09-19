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
        self.state = CoordinatorState(planner=server.make_planner('yibu', perception_qa=True))
        self.sent = []
        self.model_calls = []
        self.synth_calls = []
        owner = self

        class Socket:
            async def send(self, raw):
                owner.sent.append(json.loads(raw))

        self.ws = Socket()

        async def complete(messages, enabled):
            self.model_calls.append((messages, enabled))
            return {'choices': [{'message': {'content': 'A red square.'}}]}

        async def synth(text):
            self.synth_calls.append(text)
            return b'\x00\x01' * 800

        self.state.planner._complete_fn = complete
        self.state.synthesizer = synth

    async def feed(self, kind, payload, uid='u1'):
        await server.handle_text(self.ws, self.state, json.dumps(message(kind, payload, uid)))

    async def audio(self, uid='u1', pcm=PCM):
        await self.feed('audio_chunk', {'audio': {'encoding': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1, 'data_b64': base64.b64encode(pcm).decode()}}, uid)

    async def frame(self, uid='u1', jpeg=JPEG):
        await self.feed('frame', {'envelope': envelope(), 'jpeg_b64': base64.b64encode(jpeg).decode()}, uid)

    async def finish(self, uid='u1'):
        await self.feed('utterance_end', {}, uid)
        tasks = list(self.state.turn_tasks.values())
        if tasks:
            await asyncio.gather(*tasks)

    async def test_hello_negotiates_capture_and_audio_then_image_then_end_runs_once(self):
        await server.handle_text(self.ws, self.state, json.dumps(make_hello()))
        self.assertIs(self.sent[0]['payload']['perception_qa'], True)
        await self.audio()
        await self.frame()
        await self.finish()
        self.assertEqual([m['type'] for m in self.sent], ['hello_ok', 'turn_started', 'speak'])
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(len(self.synth_calls), 1)
        self.assertEqual(self.sent[-1]['payload']['audio']['sample_rate'], 16000)
        self.assertNotIn('u1', self.state.utterances)
        # Late data/replayed end must not open a second paid turn.
        await self.audio()
        await self.frame()
        await self.finish()
        self.assertEqual(len(self.model_calls), 1)
        self.assertNotIn('u1', self.state.utterances)

    async def test_bad_frame_and_validation_errors_do_not_log_payload(self):
        with self.assertLogs(level='INFO') as logs:
            await self.audio()
            await self.frame(jpeg=b'PRIVATE_MEDIA')
            env = envelope()
            env['camera'] = 'PRIVATE_MEDIA'
            await self.feed('frame', {'envelope': env, 'jpeg_b64': 'PRIVATE_MEDIA'})
            await self.finish()
        self.assertNotIn('PRIVATE_MEDIA', '\n'.join(logs.output))
        self.assertIn('jpeg_rejected', '\n'.join(logs.output))
        self.assertEqual(len(self.model_calls), 0)
        self.assertTrue(self.sent[-1]['payload']['text'].startswith("I couldn't get a camera image."))

    async def test_duplicate_frame_cannot_replace_first_image(self):
        await self.audio()
        await self.frame()
        await self.frame(jpeg=b'bad')
        self.assertEqual(self.state.utterances['u1'].jpeg, JPEG)

    async def test_too_short_never_calls_model_or_synth(self):
        await self.audio(pcm=b'\0' * 100)
        await self.frame()
        await self.finish()
        self.assertFalse(self.model_calls)
        self.assertFalse(self.synth_calls)

    async def test_connection_close_cancels_pending_turn_and_clears_media(self):
        started = asyncio.Event()

        async def complete(_messages, _enabled):
            started.set()
            await asyncio.Event().wait()

        self.state.planner._complete_fn = complete
        await self.audio()
        await self.frame()
        await self.feed('utterance_end', {})
        await started.wait()
        self.state.utterances['unused'] = UtteranceBuffer(pcm=bytearray(PCM), jpeg=JPEG)

        class ClosedSocket:
            async def recv(self):
                raise ConnectionError('private transport details')

        await server.handle_connection(ClosedSocket(), self.state)
        self.assertFalse(self.state.turn_tasks)
        self.assertFalse(self.state.utterances)
        self.assertFalse(self.state.context)
        self.assertIsNone(self.state.last_envelope)
        self.assertFalse(self.synth_calls)


class PerceptionWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loopback_websocket_delivers_one_image_turn_and_tone(self):
        import websockets
        from voice.test_tone import make_test_tone
        state = CoordinatorState(planner=server.make_planner('yibu', perception_qa=True))
        observed = []

        async def complete(messages, tools):
            observed.append((messages[-1]['content'][1]['image_url']['url'], tools))
            return {'choices': [{'message': {'content': 'Synthetic red square.'}}]}

        async def synth(_text):
            return make_test_tone()

        async def handler(ws):
            await server.handle_connection(ws, state)

        state.planner._complete_fn = complete
        state.synthesizer = synth
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
                speak = json.loads(await asyncio.wait_for(ws.recv(), 2))
                self.assertEqual([started['type'], speak['type']], ['turn_started', 'speak'])
                self.assertEqual(base64.b64decode(speak['payload']['audio']['data_b64']), make_test_tone())
        self.assertEqual(len(observed), 1)
        self.assertEqual(base64.b64decode(observed[0][0].split(',')[1]), JPEG)
        self.assertFalse(observed[0][1])

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
