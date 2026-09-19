#!/usr/bin/env python3
"""yibuapi direct sample: qwen3.8-omni-flash over Chat Completions.

Same OpenAI-compatible HTTP route as the qwen3.5 wrappers. Note the model
difference documented in the track research: qwen3.8-omni-flash takes
text/image/audio/video input but produces TEXT ONLY — pair it with a
separate TTS stage for voice output.
"""
from yibu_http import run_omni_cli


if __name__ == "__main__":
    raise SystemExit(run_omni_cli("qwen3.8-omni-flash", "example_qwen38_omni_flash"))
