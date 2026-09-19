"""Chunk-safe 24 kHz -> 16 kHz mono s16le via laptop ffmpeg. No network."""
from __future__ import annotations

import shutil
import subprocess

IN_RATE = 24000
OUT_RATE = 16000


def resample_24k_to_16k(pcm: bytes) -> bytes:
    """Downsample signed-16-bit little-endian mono PCM. Empty in, empty out."""
    if not pcm:
        return b""
    if len(pcm) % 2:
        raise ValueError("unaligned PCM")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg missing")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ar", str(IN_RATE), "-ac", "1", "-i", "pipe:0",
         "-ar", str(OUT_RATE), "-f", "s16le", "pipe:1"],
        input=pcm, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("ffmpeg conversion failed")
    return proc.stdout
