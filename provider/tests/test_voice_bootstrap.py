"""Offline voice-stub PCM and audio-only Yibu planner (headset bootstrap Task 3)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import unittest

from coordinator import turn
from coordinator.planner import YibuPlanner
from coordinator.server import make_planner
from coordinator.session import CoordinatorState, UtteranceBuffer
from voice.audio import BYTES_PER_SECOND

PCM_0_6S = b"\x00\x01" * int(0.6 * BYTES_PER_SECOND // 2)


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


if __name__ == "__main__":
    unittest.main()
