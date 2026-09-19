"""Echo-gate contracts on synthetic PCM. No network, no credit."""
import math
import struct
import unittest

from voice.echo_gate import envelope_correlation, should_drop, voiced_windows


def tone(seconds: float, hz: float = 440.0, amp: float = 9000.0, seed: int = 11) -> bytes:
    """Speech-like: sine with a seeded non-periodic amplitude envelope."""
    import random
    rng = random.Random(seed)
    gains = [rng.random() ** 0.5 for _ in range(int(seconds * 10) + 1)]
    n = int(16000 * seconds)
    out = []
    for i in range(n):
        g = gains[min(i // 1600, len(gains) - 1)]
        out.append(struct.pack("<h", int(amp * g * math.sin(2 * math.pi * hz * i / 16000))))
    return b"".join(out)


def noise(seconds: float, amp: float = 200.0) -> bytes:
    import random
    rng = random.Random(7)
    return b"".join(struct.pack("<h", int(rng.uniform(-amp, amp)))
                    for i in range(int(16000 * seconds)))


CLICK = b"\x00\x00" * 1600 * 3 + struct.pack("<h", 20000) + b"\x00\x00" * (1600 * 9 - 1)


class EchoGateTests(unittest.TestCase):
    def test_single_transient_has_almost_no_voiced_windows(self):
        # The device shape: one blip inside 12 otherwise-quiet chunks.
        self.assertLess(voiced_windows(CLICK), 3)

    def test_speech_like_tone_has_many(self):
        self.assertGreaterEqual(voiced_windows(tone(1.2)), 8)

    def test_same_audio_correlates_near_one_shifted_audio_too(self):
        a = tone(2.0)
        delayed = b"\x00\x00" * 1600 + a[:-1600]  # 100 ms mic delay
        self.assertGreater(envelope_correlation(a, delayed), 0.75)

    def test_unrelated_audio_scores_low(self):
        self.assertLess(envelope_correlation(tone(2.0, 440.0), tone(2.0, 440.0, seed=99)), 0.55)
        self.assertLess(envelope_correlation(tone(2.0), noise(2.0)), 0.55)

    def test_blip_drops_for_no_speech_without_calling_it_echo(self):
        drop, reason, score = should_drop(utterance_pcm=CLICK, last_speak_pcm=tone(5.0),
                                          speak_sent_at=100.0, utterance_end_at=106.0)
        self.assertTrue(drop)
        self.assertEqual(reason, "no_speech")
        self.assertEqual(score, 0.0)

    def test_echo_of_just_played_reply_drops(self):
        played = tone(6.0)
        heard_tail = played[-16000 * 2:]  # mic caught the last 2 s of the reply
        drop, reason, score = should_drop(utterance_pcm=heard_tail, last_speak_pcm=played,
                                          speak_sent_at=100.0, utterance_end_at=106.5)
        self.assertTrue(drop)
        self.assertEqual(reason, "echo")
        self.assertGreaterEqual(score, 0.55)

    def test_user_quoting_old_reply_much_later_passes(self):
        played = tone(6.0)
        drop, reason, _ = should_drop(utterance_pcm=tone(2.0, 520.0, seed=99), last_speak_pcm=played,
                                      speak_sent_at=100.0, utterance_end_at=200.0)
        self.assertFalse(drop)

    def test_real_question_passes(self):
        drop, reason, _ = should_drop(utterance_pcm=tone(2.5, 300.0, seed=99), last_speak_pcm=tone(6.0),
                                      speak_sent_at=100.0, utterance_end_at=120.0)
        self.assertFalse(drop)
        self.assertEqual(reason, "")
