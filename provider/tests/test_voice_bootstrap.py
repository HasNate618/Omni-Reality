"""Offline voice-stub PCM and audio-only Yibu planner (headset bootstrap Task 3)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import unittest
from unittest.mock import patch

from coordinator import turn
from coordinator.live_config import ensure_live_planner_config
from coordinator.planner import StubPlanner, YibuPlanner
from coordinator.server import make_planner
from coordinator.session import CoordinatorState, UtteranceBuffer
from voice.audio import BYTES_PER_SECOND
from yibu_audit import ApiKeyConfigurationError, ensure_env_api_key

def _voiced_pcm() -> bytes:
    import math as _math
    import struct as _struct
    n = int(0.6 * BYTES_PER_SECOND // 2)
    return b"".join(_struct.pack("<h", int(8000 * _math.sin(2 * _math.pi * 300 * i / 16000)))
                       for i in range(n))


# Speech-like energy: the server no-speech guard drops near-silent audio.
PCM_0_6S = _voiced_pcm()


def _utterance_buffer() -> UtteranceBuffer:
    buf = UtteranceBuffer()
    buf.pcm = bytearray(PCM_0_6S)
    return buf


def _text_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class VoiceStubTurnTests(unittest.TestCase):
    def test_voice_stub_sends_nonempty_pcm_and_no_scene_ops(self) -> None:
        from voice.test_tone import make_test_tone

        state = CoordinatorState(planner=make_planner("voice-stub"))

        async def synth(_text: str) -> bytes:
            return make_test_tone()

        state.synthesizer = synth
        sent: list[tuple[str, dict]] = []

        async def send(mtype, _turn_id, payload, _utterance_id):
            sent.append((mtype, payload))

        asyncio.run(turn._run_turn(state, send, 1, "u1", _utterance_buffer()))
        kinds = [kind for kind, _payload in sent]
        self.assertEqual(kinds, ["turn_started", "speak"])
        speak = sent[1][1]
        audio = speak["audio"]
        self.assertIsNotNone(audio)
        self.assertEqual(audio["encoding"], "pcm_s16le")
        self.assertEqual(audio["sample_rate"], 16000)
        self.assertTrue(base64.b64decode(audio["data_b64"]))

    def test_make_planner_voice_stub_has_no_spatial_ops(self) -> None:
        planner = make_planner("voice-stub")
        plan = asyncio.run(
            planner.plan(pcm=PCM_0_6S, jpeg=None, envelope=None, context=[])
        )
        self.assertEqual(plan.ops, [])


class VoiceOnlyYibuTests(unittest.TestCase):
    def test_voice_only_direct_construction_uses_voice_only_turn_purpose(self) -> None:
        planner = YibuPlanner(voice_only=True, complete_fn=lambda *a, **k: _text_response("hi"))
        self.assertEqual(planner.purpose, "voice-only-turn")

    def test_yibu_without_voice_only_keeps_voice_turn_purpose(self) -> None:
        planner = YibuPlanner(complete_fn=lambda *a, **k: _text_response("hi"))
        self.assertEqual(planner.purpose, "voice-turn")

    def test_voice_only_yibu_uses_audio_without_tools_or_ops(self) -> None:
        flags: list[bool] = []

        async def fake_text_completion(_messages, tools_enabled):
            flags.append(tools_enabled)
            return _text_response("Short spoken answer.")

        planner = YibuPlanner(voice_only=True, complete_fn=fake_text_completion)
        plan = asyncio.run(
            planner.plan(pcm=PCM_0_6S, jpeg=None, envelope=None, context=[])
        )
        self.assertEqual(plan.ops, [])
        self.assertEqual(flags, [False])
        self.assertEqual(plan.text, "Short spoken answer.")


class VoiceOnlyCliTests(unittest.TestCase):
    def _parse(self, argv: list[str]) -> argparse.Namespace:
        from coordinator import server as server_mod

        parser = argparse.ArgumentParser()
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument(
            "--planner",
            choices=["mark", "stub", "yibu", "voice-stub"],
            default="mark",
        )
        parser.add_argument("--model")
        parser.add_argument("--voice-only", action="store_true")
        server_mod._validate_cli_args(parser, parser.parse_args(argv))
        return parser.parse_args(argv)

    def test_voice_only_rejected_unless_yibu_planner(self) -> None:
        from coordinator import server as server_mod

        parser = argparse.ArgumentParser()
        parser.add_argument("--planner", choices=["mark", "stub", "yibu", "voice-stub"])
        parser.add_argument("--voice-only", action="store_true")
        for planner in ("mark", "stub", "voice-stub"):
            with self.subTest(planner=planner):
                args = parser.parse_args(["--planner", planner, "--voice-only"])
                with self.assertRaises(SystemExit) as ctx:
                    server_mod._validate_cli_args(parser, args)
                self.assertEqual(ctx.exception.code, 2)

    def test_voice_only_allowed_with_yibu(self) -> None:
        args = self._parse(["--planner", "yibu", "--voice-only"])
        self.assertTrue(args.voice_only)
        self.assertEqual(args.planner, "yibu")


class LivePlannerConfigTests(unittest.TestCase):
    def test_missing_yibu_api_key_raises_configuration_error(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "YIBU_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ApiKeyConfigurationError) as ctx:
                ensure_env_api_key("YIBU_API_KEY")
            self.assertEqual(ctx.exception.name, "YIBU_API_KEY")
            self.assertEqual(str(ctx.exception), "YIBU_API_KEY")

    def test_live_yibu_startup_requires_api_key(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "YIBU_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ApiKeyConfigurationError):
                ensure_live_planner_config("yibu")

    def test_live_startup_skipped_for_other_planners(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "YIBU_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            ensure_live_planner_config("voice-stub")

    def test_run_server_voice_only_missing_key_never_binds_websocket(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "YIBU_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch("websockets.serve") as serve_mock:
                from coordinator.server import run_server

                with self.assertRaises(ApiKeyConfigurationError):
                    asyncio.run(run_server(planner_kind="yibu", voice_only=True, port=9876))
                serve_mock.assert_not_called()

    def test_run_server_tool_planner_missing_key_never_binds_websocket(self) -> None:
        # The tool planner is the demo's own mode (`--planner yibu`, no flags)
        # and reaches the provider on its first turn. Without this check the
        # server binds happily and then every turn fails behind a line that
        # reads like a network fault instead of a missing key.
        env = {k: v for k, v in os.environ.items() if k != "YIBU_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch("websockets.serve") as serve_mock:
                from coordinator.server import run_server

                with self.assertRaises(ApiKeyConfigurationError):
                    asyncio.run(run_server(planner_kind="yibu", port=9877))
                serve_mock.assert_not_called()


class PlannerSystemExitTurnTests(unittest.TestCase):
    def test_system_exit_planner_emits_redacted_failure_and_fallback(self) -> None:
        state = CoordinatorState(planner=StubPlanner(error=SystemExit(1)))
        sent: list[tuple[str, dict]] = []

        async def send(mtype, _turn_id, payload, _utterance_id):
            sent.append((mtype, payload))

        logger = logging.getLogger("voice.bootstrap_diagnostics")
        records: list[str] = []

        class Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = Handler()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            asyncio.run(turn._run_turn(state, send, 1, "u1", _utterance_buffer()))
        finally:
            logger.removeHandler(handler)

        kinds = [kind for kind, _payload in sent]
        self.assertEqual(kinds, ["turn_started", "speak"])
        self.assertEqual(sent[1][1]["text"], turn.SAY_MODEL_ERROR)
        failed_lines = [line for line in records if "planner_failed" in line]
        self.assertEqual(len(failed_lines), 1)
        self.assertIn("exception_class=SystemExit", failed_lines[0])
        self.assertNotIn("secret", failed_lines[0].lower())

    def test_connection_survives_planner_system_exit(self) -> None:
        from tests.test_coordinator import DummyWs, make_hello, wait_for
        from coordinator.server import CoordinatorState, handle_connection

        async def scenario():
            state = CoordinatorState(planner=StubPlanner(error=SystemExit(1)))
            ws = DummyWs()
            conn = asyncio.create_task(handle_connection(ws, state))
            await ws.inject(make_hello())
            await wait_for(ws, lambda m: m["type"] == "hello_ok")
            await ws.inject(
                {
                    "v": 1,
                    "type": "audio_chunk",
                    "session_id": None,
                    "turn_id": 0,
                    "utterance_id": "u1",
                    "payload": {
                        "utterance_id": "u1",
                        "t_unix_ns": 0,
                        "audio": {
                            "encoding": "pcm_s16le",
                            "sample_rate": 16000,
                            "channels": 1,
                            "data_b64": base64.b64encode(PCM_0_6S).decode("ascii"),
                        },
                    },
                }
            )
            await ws.inject(
                {
                    "v": 1,
                    "type": "utterance_end",
                    "session_id": None,
                    "turn_id": 0,
                    "utterance_id": "u1",
                    "payload": {"utterance_id": "u1", "t_unix_ns": 0},
                }
            )
            await wait_for(ws, lambda m: m["type"] == "speak")
            if conn.done():
                raise AssertionError("connection task exited after planner SystemExit")
            conn.cancel()
            try:
                await conn
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())


class TurnTaskDoneCallbackTests(unittest.TestCase):
    def test_done_callback_logs_unhandled_exception_class_only(self) -> None:
        state = CoordinatorState(planner=StubPlanner())
        records: list[str] = []

        class Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = Handler()
        turn.logger.addHandler(handler)
        turn.logger.setLevel(logging.INFO)
        try:

            async def boom() -> None:
                raise ValueError("must not appear in logs")

            async def run_boom() -> None:
                task = asyncio.create_task(boom())
                state.turn_tasks[99] = task
                task.add_done_callback(lambda t: turn._turn_task_done(state, 99, t))
                try:
                    await task
                except ValueError:
                    pass

            asyncio.run(run_boom())
        finally:
            turn.logger.removeHandler(handler)

        self.assertNotIn(99, state.turn_tasks)
        self.assertEqual(len(records), 1)
        self.assertIn("turn_id=99", records[0])
        self.assertIn("exception_class=ValueError", records[0])
        self.assertNotIn("must not appear", records[0])


if __name__ == "__main__":
    unittest.main()
