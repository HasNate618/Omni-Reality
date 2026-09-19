"""Omni provider model registry (yibuapi gateway).

Single source of truth for the models enabled on this team's key.
HTTP models go through OpenAI-compatible Chat Completions;
realtime/live models go through their WebSocket routes.
"""
from __future__ import annotations

BASE_URL = "https://yibuapi.com/v1"

# (model_id, transport, endpoint, CLI entry point)
MODELS: dict[str, dict[str, str]] = {
    "qwen3.5-omni-flash": {
        "transport": "http",
        "endpoint": f"{BASE_URL}/chat/completions",
        "script": "qwen35_omni_flash.py",
        "notes": "HTTP Chat Completions. Text + image + audio in.",
    },
    "qwen3.5-omni-plus": {
        "transport": "http",
        "endpoint": f"{BASE_URL}/chat/completions",
        "script": "qwen35_omni_plus.py",
        "notes": "HTTP Chat Completions. Text + image + audio in.",
    },
    "qwen3.5-omni-plus-realtime": {
        "transport": "websocket",
        "endpoint": "wss://yibuapi.com/v1/realtime",
        "script": "qwen35_omni_plus_realtime.py",
        "notes": "OpenAI-realtime-style WebSocket (?model=). Single-turn text in the example.",
    },
    "qwen3.8-omni-flash": {
        "transport": "http",
        "endpoint": f"{BASE_URL}/chat/completions",
        "script": "qwen38_omni_flash.py",
        "notes": "HTTP Chat Completions. Multimodal in, TEXT OUT ONLY — needs separate TTS for voice.",
    },
    "gemini-3.1-flash-live-preview": {
        "transport": "websocket",
        "endpoint": "wss://yibuapi.com/v1beta/gemini/live",
        "script": "gemini31_flash_live.py",
        "notes": "Gemini Live WebSocket. Returns AUDIO; text comes via outputAudioTranscription.",
    },
}

HTTP_MODELS = [m for m, v in MODELS.items() if v["transport"] == "http"]
WS_MODELS = [m for m, v in MODELS.items() if v["transport"] == "websocket"]
