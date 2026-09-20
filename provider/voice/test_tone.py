"""Deterministic offline PCM tone for voice-stub transport checks (no provider I/O)."""

from __future__ import annotations

import math
import struct

_DEFAULT_HZ = 440.0
_DEFAULT_AMPLITUDE = 8000


def make_test_tone(sample_rate: int = 16000, duration_ms: int = 250) -> bytes:
    """Signed 16-bit mono tone, bounded and deterministic."""
    n_samples = max(1, sample_rate * duration_ms // 1000)
    out = bytearray()
    for i in range(n_samples):
        t = i / sample_rate
        sample = int(_DEFAULT_AMPLITUDE * math.sin(2.0 * math.pi * _DEFAULT_HZ * t))
        sample = max(-32767, min(32767, sample))
        out += struct.pack("<h", sample)
    return bytes(out)
