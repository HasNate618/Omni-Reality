"""Cloud speech adapter (voice spec §2 step 7): final line -> raw PCM.

Primary live route is the documented Gemini Live WebSocket, which returns
audio bytes (`gemini31_flash_live.gemini_live_call` with `--audio-out`
semantics). Tests inject `synth_fn`; no synth configured returns None and
the turn stays caption-only (degraded, never claimed as voice proof).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
ENCODING = "pcm_s16le"

SynthFn = Callable[[str], Awaitable[bytes | None]]


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
    if synth_fn is not None:
        try:
            result = synth_fn(text)
            if asyncio.iscoroutine(result):
                result = await result
            return result or None
        except Exception:
            logger.exception("injected speech synth failed")
            return None
    return await asyncio.to_thread(_gemini_speech, text, purpose, timeout)


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
            )
        except Exception:
            logger.exception("gemini speech leg failed")
            return None
        if not out.exists():
            return None
        data = out.read_bytes()
        return data or None


def audio_block(pcm: bytes) -> dict:
    """Wrap raw PCM for the `speak.audio` payload shape."""
    import base64

    return {
        "encoding": ENCODING,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "data_b64": base64.b64encode(pcm).decode(),
    }
