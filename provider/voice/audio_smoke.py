#!/usr/bin/env python3
"""Live smoke: does mic audio actually reach the omni model? (spends credit)

Run from provider/:
    python -m voice.audio_smoke --say "mark the laptop" --image photo.jpg
    python -m voice.audio_smoke --wav artifacts/utt1.wav --audio-as raw_b64

The prompt asks the model to repeat what it heard, so a correct transcript
in the reply proves the audio bytes were used (spec §14 slice-3 pass/fail).
Prints one JSON line per call; no request content goes to the audit log.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from voice.audio import build_voice_messages, pcm_seconds, pcm_to_wav_bytes, read_wav_pcm, say_to_pcm
from yibu_http import chat_completion, require_api_key

PROMPT = (
    "Transcribe exactly what the speaker says in the audio, then in one short "
    "sentence say what you see in the image if there is one. Reply as JSON: "
    '{"heard": "...", "seen": "..."}'
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--wav", type=Path, help="16 kHz mono s16 WAV")
    src.add_argument("--say", help="synthesize this text with macOS say")
    parser.add_argument("--image", type=Path, help="optional JPEG")
    parser.add_argument("--model", default="qwen3.8-omni-flash")
    parser.add_argument("--audio-as", choices=["data_url", "raw_b64"], default="data_url")
    parser.add_argument("--no-audio", action="store_true", help="control call: drop the audio part")
    parser.add_argument("--purpose", default="voice-smoke")
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args()

    pcm = read_wav_pcm(args.wav) if args.wav else say_to_pcm(args.say)
    wav = None if args.no_audio else pcm_to_wav_bytes(pcm)
    jpeg = args.image.read_bytes() if args.image else None
    messages = build_voice_messages(PROMPT, wav=wav, jpeg=jpeg, audio_as=args.audio_as)

    started = time.monotonic()
    result = {
        "model": args.model,
        "audio_as": None if wav is None else args.audio_as,
        "spoken": args.say,
        "audio_s": round(pcm_seconds(pcm), 2),
        "wav_bytes_sent": len(wav) if wav else 0,
        "jpeg_bytes_sent": len(jpeg) if jpeg else 0,
    }
    try:
        text, _, record = chat_completion(
            api_key=require_api_key(),
            model=args.model,
            messages=messages,
            purpose=args.purpose,
            max_tokens=args.max_tokens,
        )
        result.update(
            ok=True,
            latency_s=round(time.monotonic() - started, 2),
            reply=text,
            call_id=record.get("call_id"),
            input_tokens=record.get("input_tokens"),
            output_tokens=record.get("output_tokens"),
            usage_raw=record.get("usage_raw"),
        )
    except Exception as exc:  # noqa: BLE001 - report every gateway failure shape
        result.update(ok=False, latency_s=round(time.monotonic() - started, 2), error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
