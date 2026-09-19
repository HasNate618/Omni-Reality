"""Drop phantom utterances before they cost a model call. Pure functions.

Two shapes observed on device: single-transient blips (exactly 12 chunks:
4 pre-roll + 8 silence, zero sustained voicing) and speaker echo (the mic
re-hearing our own just-played reply). Both always find something to say
because every turn attaches a fresh camera frame.
"""
from __future__ import annotations

import math
import struct

WINDOW = 1600  # 100 ms at 16 kHz mono s16le
VOICED_RMS = 0.01
MIN_VOICED_WINDOWS = 2
BLOCK = 320  # 20 ms energy blocks for the envelope correlator
MAX_LAG_BLOCKS = 50  # +-1 s of playback/mic misalignment
ECHO_SCORE_DROP = 0.55
ECHO_RECENCY_S = 8.0
ECHO_OVERLAP_S = 1.0


def window_rms(pcm: bytes, start: int, count: int) -> float:
    total = 0
    for i in range(start, start + count):
        sample = struct.unpack_from("<h", pcm, i * 2)[0] / 32768.0
        total += sample * sample
    return math.sqrt(total / max(1, count))


def voiced_windows(pcm: bytes) -> int:
    """Windows at/above the VAD floor. A lone click scores ~1, speech many."""
    if len(pcm) < WINDOW * 2:
        return 0
    return sum(1 for off in range(0, len(pcm) // 2 - WINDOW + 1, WINDOW)
               if window_rms(pcm, off, WINDOW) >= VOICED_RMS)


def peak_rms(pcm: bytes) -> float:
    """Loudest 100 ms window. Tuning data only, never content."""
    if len(pcm) < WINDOW * 2:
        return 0.0
    return max(window_rms(pcm, off, WINDOW)
               for off in range(0, len(pcm) // 2 - WINDOW + 1, WINDOW))


def envelope(data: bytes) -> list[float]:
    samples = len(data) // 2
    return [sum(abs(struct.unpack_from("<h", data, (b * BLOCK + i) * 2)[0])
                    for i in range(min(BLOCK, samples - b * BLOCK))) / BLOCK
            for b in range((samples + BLOCK - 1) // BLOCK)]


def envelope_correlation(a: bytes, b: bytes) -> float:
    """Peak normalized envelope dot over +-1 s lag. Same audio ~= 1.0."""
    ea, eb = envelope(a), envelope(b)
    if len(ea) < 4 or len(eb) < 4:
        return 0.0
    ma = sum(ea) / len(ea)
    mb = sum(eb) / len(eb)
    na = [v - ma for v in ea]
    nb = [v - mb for v in eb]
    denom = math.sqrt(sum(v * v for v in na) * sum(v * v for v in nb))
    if denom <= 0:
        return 0.0
    best = 0.0
    for lag in range(-min(MAX_LAG_BLOCKS, len(ea) - 1),
                     min(MAX_LAG_BLOCKS, len(eb) - 1) + 1):
        total = 0.0
        for i, va in enumerate(na):
            j = i + lag
            if 0 <= j < len(nb):
                total += va * nb[j]
        best = max(best, total / denom)
    return best


def should_drop(*, utterance_pcm: bytes, last_speak_pcm: bytes | None,
                speak_sent_at: float | None, utterance_end_at: float,
                speak_rate: float = 32000.0) -> tuple[bool, str, float]:
    """(drop, reason, echo_score). Score is always computed for tuning data."""
    score = 0.0
    if voiced_windows(utterance_pcm) < MIN_VOICED_WINDOWS:
        return True, "no_speech", score
    if last_speak_pcm and speak_sent_at is not None:
        utt_s = len(utterance_pcm) / speak_rate
        if utterance_end_at - utt_s < speak_sent_at + len(last_speak_pcm) / speak_rate + ECHO_OVERLAP_S:
            if utterance_end_at - speak_sent_at < ECHO_RECENCY_S + utt_s:
                # The mic hears the tail of playback, not the whole reply:
                # correlate against the matching tail so lengths normalize.
                need = len(utterance_pcm) + int(speak_rate)  # +1 s margin
                tail = last_speak_pcm[-need:] if len(last_speak_pcm) > need else last_speak_pcm
                score = envelope_correlation(utterance_pcm, tail)
                if score >= ECHO_SCORE_DROP:
                    return True, "echo", score
    return False, "", score
