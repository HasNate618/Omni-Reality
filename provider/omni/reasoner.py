"""Bounded Omni chat tool loop with PCM+JPEG gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from yibu_http import extract_text, extract_tool_calls

CompleteFn = Callable[..., Awaitable[dict[str, Any]]]
ExecuteFn = Callable[..., Awaitable[Any]]

MIN_PCM_BYTES = 16000  # 0.5 s of s16le mono @ 16 kHz


@dataclass
class ReasonerTurn:
    pcm_bytes: bytes
    jpeg_b64: str | None
    frame_id: str | None = None


def require_omni_inputs(pcm_bytes: bytes, jpeg_b64: str | None) -> str | None:
    if len(pcm_bytes) < MIN_PCM_BYTES:
        return "pcm_too_short"
    if not jpeg_b64:
        return "missing_jpeg"
    return None


async def run_tool_loop(
    *,
    complete_fn: CompleteFn,
    execute_fn: ExecuteFn,
    messages: list[dict[str, Any]],
    max_rounds: int = 4,
) -> tuple[list[dict], str]:
    collected_ops: list[dict] = []
    final_text = ""

    for round_index in range(max_rounds):
        response = await complete_fn(messages, True)
        tool_calls = extract_tool_calls(response)
        if not tool_calls:
            final_text = extract_text(response)
            break

        assistant_message = (response.get("choices") or [{}])[0].get("message") or {}
        # Rebuild the tool_calls echo in canonical form with a GUARANTEED non-empty
        # id, instead of echoing the raw structure back.
        #
        # extract_tool_calls coerces a missing id to "", and this used to re-send
        # the raw tool_calls while the tool message used that coerced id -- so a
        # gateway that omits ids got {"role":"tool","tool_call_id":""} and
        # answered 400. That 400 lands on the SECOND call, i.e. AFTER the tool has
        # already run: a placement got queued, no box was ever planted, and the
        # wearer heard nothing. Observed live on turn 3 -- POST /jobs 200, then
        # 400, then "turn background task failed ... ValueError".
        canonical: list[dict[str, Any]] = []
        for index, call in enumerate(tool_calls):
            call_id = call.get("id") or f"call_{round_index}_{index}"
            call["id"] = call_id
            canonical.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                },
            })
        messages.append({
            "role": "assistant",
            # Never an explicit null: some gateways reject content:null when
            # tool_calls are present.
            "content": assistant_message.get("content") or "",
            "tool_calls": canonical,
        })

        for call in tool_calls:
            name = call["name"]
            arguments = call.get("arguments") or {}
            if name == "emit_scene_ops":
                for op in arguments.get("ops") or []:
                    if isinstance(op, dict) and len(collected_ops) < 3:
                        collected_ops.append(op)
                tool_content = {"ok": True}
            else:
                tool_result = await execute_fn(name, arguments)
                if isinstance(tool_result, dict):
                    sanitized = {k: v for k, v in tool_result.items() if k != "mask_png_b64"}
                    tool_content = sanitized
                else:
                    tool_content = tool_result
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(tool_content),
            })

    return collected_ops, final_text
