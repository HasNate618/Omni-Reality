#!/usr/bin/env python3
"""Live smoke for Omni tool definitions (not imported by unittest)."""

from __future__ import annotations

import argparse
import sys

from omni.tools import TOOL_DEFINITIONS
from yibu_audit import require_env_api_key
from yibu_http import build_omni_messages, chat_completion, extract_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Omni tool-loop live smoke")
    parser.add_argument("--purpose", default="smoke_omni_tools")
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--model", default="qwen3.8-omni-flash")
    args = parser.parse_args()

    try:
        api_key = require_env_api_key("YIBU_API_KEY")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    messages = build_omni_messages(
        "Reply with one short word only.",
        system="You are a smoke test assistant.",
    )
    _text, _raw, _audit = chat_completion(
        api_key=api_key,
        model=args.model,
        messages=messages,
        purpose=args.purpose,
        max_tokens=args.max_tokens,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
    )
    print(extract_text(_raw) or "(empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
