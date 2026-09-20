"""VoiceBootstrap redacted diagnostic helpers (no media in logs)."""

from __future__ import annotations

import logging
import unittest

from voice import bootstrap_diagnostics as diag


class VoiceBootstrapDiagnosticsTests(unittest.TestCase):
    def test_rejects_caption_like_string_fields(self) -> None:
        self.assertFalse(diag.is_allowed_field("text", "hello there"))
        self.assertFalse(diag.is_allowed_field("pcm_bytes", "not-a-number"))

    def test_allows_count_and_gate_fields(self) -> None:
        self.assertTrue(diag.is_allowed_field("pcm_bytes", 48000))
        self.assertTrue(diag.is_allowed_field("voice_gate", "passed"))
        self.assertTrue(diag.is_allowed_field("exception_class", "RuntimeError"))

    def test_emit_never_includes_payload_strings(self) -> None:
        with self.assertRaises(ValueError):
            diag._emit("turn_started", turn_id=1, mode="voice_only", pcm_bytes="AAAA")

    def test_turn_started_log_line_shape(self) -> None:
        records: list[str] = []

        class Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        logger = logging.getLogger("voice.bootstrap_diagnostics")
        handler = Handler()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            diag.turn_started(turn_id=3, mode="voice_stub", pcm_bytes=19200)
        finally:
            logger.removeHandler(handler)

        self.assertEqual(1, len(records))
        line = records[0]
        self.assertTrue(line.startswith("VoiceBootstrap component=coordinator"))
        self.assertIn("event=turn_started", line)
        self.assertIn("turn_id=3", line)
        self.assertIn("pcm_bytes=19200", line)
        self.assertNotIn("AAAA", line)


if __name__ == "__main__":
    unittest.main()
