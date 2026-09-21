"""Cloud speech format regressions. No provider calls or real microphone data."""
from __future__ import annotations

import asyncio
import base64
import json
import math
import struct
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

from voice.cloud_speech import _gemini_speech, audio_block, synthesize_line


class CloudSpeechFormatTests(unittest.TestCase):
    def test_provider_24khz_audio_plays_at_original_duration_and_pitch(self):
        # Relabeling 24k as 16k makes one second last 1.5s at the wrong pitch.
        samples = [round(10000 * math.sin(2 * math.pi * 1000 * i / 24000))
                   for i in range(24000)]
        provider_pcm = struct.pack("<24000h", *samples)

        def provider_call(**kwargs):
            kwargs["audio_out"].write_bytes(provider_pcm)
            return "test-only reply", {}

        with patch("yibu_audit.require_env_api_key", return_value="test-only"), \
             patch("gemini31_flash_live.gemini_live_call", side_effect=provider_call):
            pcm = _gemini_speech("test-only line", "offline-format-test", 1)

        if pcm is None:
            self.fail("speech conversion returned no PCM")
        self.assertEqual(audio_block(pcm)["sample_rate"], 16000)
        self.assertEqual(len(pcm), 32000)  # one second, 16000 signed-16 samples
        decoded = struct.unpack("<16000h", pcm)
        upward_crossings = sum(a <= 0 < b for a, b in zip(decoded, decoded[1:]))
        self.assertAlmostEqual(upward_crossings, 1000, delta=2)
        self.assertGreater(max(decoded), 9000)
        self.assertLess(min(decoded), -9000)

    def test_malformed_provider_pcm_is_not_sent_to_headset(self):
        def provider_call(**kwargs):
            kwargs["audio_out"].write_bytes(b"\x01\x02\x03")
            return "test-only reply", {}

        with patch("yibu_audit.require_env_api_key", return_value="test-only"), \
             patch("gemini31_flash_live.gemini_live_call", side_effect=provider_call):
            self.assertIsNone(_gemini_speech("test line", "offline-format-test", 1))

    def test_provider_failure_logs_type_not_exception_message(self):
        with patch("yibu_audit.require_env_api_key", return_value="test-only"), \
             patch("gemini31_flash_live.gemini_live_call",
                   side_effect=RuntimeError("private-response-sentinel")), \
             self.assertLogs("voice.cloud_speech", level="WARNING") as captured:
            self.assertIsNone(_gemini_speech("test line", "offline-format-test", 1))
        logs = "\n".join(captured.output)
        self.assertIn("RuntimeError", logs)
        self.assertNotIn("private-response-sentinel", logs)

    def test_injected_awaitable_is_resolved_to_pcm(self):
        async def run():
            future = asyncio.get_running_loop().create_future()
            future.set_result(b"\x00\x00" * 10)
            return await synthesize_line(text="test", synth_fn=lambda _text: future)

        self.assertEqual(asyncio.run(run()), b"\x00\x00" * 10)

    def test_missing_converter_returns_no_audio_without_error_content(self):
        def provider_call(**kwargs):
            kwargs["audio_out"].write_bytes(b"\x00\x00" * 100)
            return "test-only reply", {}

        with patch("yibu_audit.require_env_api_key", return_value="test-only"), \
             patch("gemini31_flash_live.gemini_live_call", side_effect=provider_call), \
             patch("voice.cloud_speech.subprocess.run",
                   side_effect=FileNotFoundError("private-path-sentinel")), \
             self.assertLogs("voice.cloud_speech", level="WARNING") as captured:
            self.assertIsNone(_gemini_speech("test", "offline-format-test", 1))
        self.assertNotIn("private-path-sentinel", "\n".join(captured.output))


class GeminiPcmMetadataTests(unittest.TestCase):
    def test_format_validation_precedes_writing_audio_and_audits_failures(self):
        from gemini31_flash_live import gemini_live_call

        for mime, accepted in [("audio/pcm;rate=24000", True),
                               ("audio/pcm;rate=16000", False),
                               ("audio/pcm;rate=24000;channels=2", False),
                               ("audio/wav;rate=24000", False), (None, False)]:
            with self.subTest(mime=mime), tempfile.TemporaryDirectory() as tmp:
                audio_path = Path(tmp) / "speech.pcm"
                audit_path = Path(tmp) / "audit.jsonl"
                ws = MagicMock()
                ws.recv.side_effect = [json.dumps({"setupComplete": {}}), json.dumps({
                    "serverContent": {"turnComplete": True, "modelTurn": {
                        "parts": [{"inlineData": {"mimeType": mime,
                                  "data": base64.b64encode(b"\x00\x00" * 10).decode()}}]
                    }}
                })]
                with patch("gemini31_flash_live.connect") as connect:
                    connect.return_value.__enter__.return_value = ws
                    call = partial(gemini_live_call, api_key="test-only", model="test-model",
                                   prompt="test", purpose="offline-format-test",
                                   endpoint="wss://invalid.example", audit_log=audit_path,
                                   audio_out=audio_path, expected_audio_sample_rate=24000)
                    if accepted:
                        call()
                        self.assertEqual(audio_path.read_bytes(), b"\x00\x00" * 10)
                    else:
                        with self.assertRaises(ValueError):
                            call()
                        self.assertFalse(audio_path.exists())
                record = json.loads(audit_path.read_text())
                self.assertEqual(record["ok"], accepted)


if __name__ == "__main__":
    unittest.main()
