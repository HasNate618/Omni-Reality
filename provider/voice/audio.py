"""Mic PCM helpers for the voice turn (spec §6 audio, §10 omni input).

Quest streams 16 kHz mono pcm_s16le in `audio_chunk`s. The yibu omni HTTP
route takes audio as an `input_audio` content part holding a WAV, so the
coordinator wraps the buffered PCM in a WAV container before the call.
"""

from __future__ import annotations

import base64
import io
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
MIN_UTTERANCE_S = 0.5


def pcm_seconds(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / (sample_rate * CHANNELS * SAMPLE_WIDTH)


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container (no resampling)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def read_wav_pcm(path: Path) -> bytes:
    """Return PCM frames from a 16 kHz mono s16 WAV; reject anything else."""
    with wave.open(str(path), "rb") as wav:
        shape = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        if shape != (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH):
            raise ValueError(
                f"{path}: need 16000 Hz mono 16-bit, got {shape[0]} Hz "
                f"{shape[1]}ch {shape[2] * 8}-bit. Convert with: "
                f"afconvert -f WAVE -d LEI16@16000 -c 1 in.wav out.wav"
            )
        return wav.readframes(wav.getnframes())


def say_to_pcm(text: str) -> bytes:
    """Synthesize speech with macOS `say` as 16 kHz mono PCM (test input)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "say.wav"
        subprocess.run(
            ["say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000", text],
            check=True,
        )
        return read_wav_pcm(out)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def build_voice_messages(
    prompt: str,
    *,
    wav: bytes | None,
    jpeg: bytes | None = None,
    system: str | None = None,
    audio_as: str = "data_url",
) -> list[dict[str, Any]]:
    """Chat Completions messages with in-memory WAV/JPEG bytes.

    Same part shapes as `yibu_http.build_omni_messages`, which only takes
    file paths. `audio_as="raw_b64"` sends bare base64 instead of a data
    URL, for gateways that reject the data-URL form.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if jpeg is not None:
        content.append(
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _b64(jpeg)}}
        )
    if wav is not None:
        data = _b64(wav) if audio_as == "raw_b64" else "data:audio/wav;base64," + _b64(wav)
        content.append({"type": "input_audio", "input_audio": {"data": data, "format": "wav"}})
    messages.append({"role": "user", "content": content})
    return messages
