"""Windowed resampler contracts on synthetic tones. No network, no credit."""
import math
import struct
import unittest

from voice.resample import resample_24k_to_16k


def sine_24k(seconds: float, hz: float = 440.0) -> bytes:
    n = int(24000 * seconds)
    return b"".join(struct.pack("<h", int(10000 * math.sin(2 * math.pi * hz * i / 24000)))
                    for i in range(n))


class ResampleTests(unittest.TestCase):
    def test_one_second_tone_shrinks_by_ratio(self):
        out = resample_24k_to_16k(sine_24k(1.0))
        self.assertEqual(len(out), 16000 * 2)

    def test_empty_in_empty_out(self):
        self.assertEqual(resample_24k_to_16k(b""), b"")

    def test_unaligned_rejected(self):
        with self.assertRaises(ValueError):
            resample_24k_to_16k(b"\x00")

    def test_output_is_valid_16k_wav_body(self):
        from voice.audio import pcm_to_wav_bytes
        wav = pcm_to_wav_bytes(resample_24k_to_16k(sine_24k(0.5)), sample_rate=16000)
        self.assertTrue(wav.startswith(b"RIFF"))
