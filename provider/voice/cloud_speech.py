"""Cloud speech adapter (voice spec §2 step 7): final line -> raw PCM.

Primary live route is the documented Gemini Live WebSocket, which returns
audio bytes (`gemini31_flash_live.gemini_live_call` with `--audio-out`
semantics). Tests inject `synth_fn`; no synth configured returns None and
the turn stays caption-only (degraded, never claimed as voice proof).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import subprocess  # noqa: F401  # patch anchor for voice.resample's ffmpeg call
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# What the headset plays (SpeakCloudPlayer.SampleRate). The live route emits
# PROVIDER_SAMPLE_RATE, so its bytes are converted before being labelled with
# this: relabelling 24 kHz as 16 kHz makes one second last 1.5 s, at the wrong
# pitch.
SAMPLE_RATE = 16000
PROVIDER_SAMPLE_RATE = 24000
CHANNELS = 1
ENCODING = "pcm_s16le"

SynthFn = Callable[[str], Awaitable[bytes | None]]


# Short fixed lines ("Tracking that now.") repeat every turn and cost ~2 s of
# synthesis each time, which is what separated the mask from the voice. Keep
# them; the set of distinct lines in a session is tiny.
_CACHE: dict[str, bytes] = {}
_CACHE_MAX = 32


def cached_line(text: str) -> bytes | None:
    """PCM for a line already synthesized this session, if any."""
    return _CACHE.get((text or "").strip())


async def warm_line(text: str, purpose: str = "voice-speak") -> bool:
    """Synthesize a line ahead of time so the turn that needs it does not wait."""
    return await synthesize_line(text=text, purpose=purpose) is not None


async def synthesize_line(
    *,
    text: str,
    synth_fn: SynthFn | None = None,
    purpose: str = "voice-speak",
    timeout: float = 60.0,
) -> bytes | None:
    """Speak `text` through cloud TTS. Returns s16le mono 16 kHz PCM or None."""
    if not text or not text.strip():
        return None
    hit = _CACHE.get(text.strip())
    if hit is not None:
        return hit
    if synth_fn is not None:
        try:
            result = synth_fn(text)
            # isawaitable, not iscoroutine: a caller may hand back a Future, which
            # is awaitable but is not a coroutine.
            if inspect.isawaitable(result):
                result = await result
            return result or None
        except Exception:
            logger.exception("injected speech synth failed")
            return None
    pcm = await asyncio.to_thread(_gemini_speech, text, purpose, timeout)
    if pcm:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[text.strip()] = pcm
    return pcm


def _gemini_speech(text: str, purpose: str, timeout: float) -> bytes | None:
    from gemini31_flash_live import (
        DEFAULT_ENDPOINT,
        DEFAULT_MODEL,
        gemini_live_call,
    )
    from yibu_audit import require_env_api_key

    with tempfile.TemporaryDirectory(prefix="omni_speak_") as tmp:
        out = Path(tmp) / "speak.pcm"
        try:
            gemini_live_call(
                api_key=require_env_api_key(),
                model=DEFAULT_MODEL,
                prompt="Say exactly: " + text.strip(),
                purpose=purpose,
                endpoint=DEFAULT_ENDPOINT,
                audit_log=None,
                audio_out=out,
                timeout=timeout,
                # Declare the rate we actually expect, so a provider that changes
                # format is rejected here instead of being mislabelled later.
                expected_audio_sample_rate=PROVIDER_SAMPLE_RATE,
            )
        except Exception as exc:
            # Type only. Provider errors can carry response text and the headset
            # logs are not a safe place for it.
            logger.warning("gemini speech leg failed: %s", type(exc).__name__)
            return None
        if not out.exists():
            return None
        data = out.read_bytes()
    if not data or len(data) % 2:
        # Not whole s16le samples: refuse rather than hand the headset audio we
        # cannot describe.
        logger.warning("gemini speech leg returned malformed PCM")
        return None
    # The Live model speaks at 24 kHz. audio_block labels the payload
    # SAMPLE_RATE (16 kHz), so handing the raw bytes over made Quest play
    # them 1.5x too slow. The B-mode streaming path already resamples
    # every chunk; do the same for the one-shot line.
    try:
        from voice.resample import resample_24k_to_16k

        return resample_24k_to_16k(data) or None
    except Exception as exc:
        # Type only, for the same reason as above: the converter raises with
        # paths and ffmpeg stderr attached.
        logger.info("speech resample failed exception_class=%s", type(exc).__name__)
        return None


def audio_block(pcm: bytes) -> dict:
    """Wrap raw PCM for the `speak.audio` payload shape."""
    import base64

    return {
        "encoding": ENCODING,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "data_b64": base64.b64encode(pcm).decode(),
    }
