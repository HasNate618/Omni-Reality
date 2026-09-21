"""The tool loop must send a WELL-FORMED conversation back to the gateway.

A live turn crashed after the tool had already run: yibuapi answered 400 on the
follow-up call, the turn died with ValueError, the placement that had been queued
was never planted, and the wearer heard nothing. The cause is the message we build
for the second call:

  * `extract_tool_calls` coerces a missing `id` to "", and the tool result was sent
    as {"role":"tool","tool_call_id":""} -- malformed, so 400.
  * the raw `tool_calls` structure was echoed back rather than rebuilt.
  * the assistant `content` could be an explicit null.

These tests need no API key and no network: they assert on the messages the loop
would send, which is exactly what the gateway rejected.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from omni.reasoner import run_tool_loop


def _reply_with_tool_call(*, call_id: str | None, content=None) -> dict:
    call: dict = {"function": {"name": "place_item", "arguments": '{"name":"oak side table"}'}}
    if call_id is not None:
        call["id"] = call_id
    return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": [call]}}]}


class ToolLoopMessageShapeTests(unittest.TestCase):
    """Assert on the messages sent on the SECOND call -- the one that 400'd."""

    def _second_call_messages(self, first_response: dict) -> list[dict]:
        seen: list[list[dict]] = []

        async def complete_fn(messages, tools_enabled):
            seen.append([dict(m) for m in messages])
            if len(seen) == 1:
                return first_response
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

        async def execute_fn(name, arguments):
            return {"listed": "oak side table", "extent_m": [0.55, 0.40, 0.72]}

        asyncio.run(run_tool_loop(
            complete_fn=complete_fn,
            execute_fn=execute_fn,
            messages=[{"role": "user", "content": "bring this to life"}],
            max_rounds=3,
        ))
        self.assertGreaterEqual(len(seen), 2, "the loop should have made a follow-up call")
        return seen[1]

    def test_missing_id_becomes_a_non_empty_id(self) -> None:
        msgs = self._second_call_messages(_reply_with_tool_call(call_id=None))
        assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        tool = [m for m in msgs if m.get("role") == "tool"][-1]
        self.assertTrue(assistant["tool_calls"][0]["id"], "assistant tool_call id must not be empty")
        self.assertTrue(tool["tool_call_id"], "tool_call_id must not be empty (this caused the 400)")

    def test_tool_call_id_matches_the_assistant_echo(self) -> None:
        msgs = self._second_call_messages(_reply_with_tool_call(call_id=None))
        assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        tool = [m for m in msgs if m.get("role") == "tool"][-1]
        self.assertEqual(tool["tool_call_id"], assistant["tool_calls"][0]["id"])

    def test_supplied_id_is_preserved(self) -> None:
        msgs = self._second_call_messages(_reply_with_tool_call(call_id="call_abc123"))
        assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        tool = [m for m in msgs if m.get("role") == "tool"][-1]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_abc123")
        self.assertEqual(tool["tool_call_id"], "call_abc123")

    def test_assistant_content_is_never_null(self) -> None:
        msgs = self._second_call_messages(_reply_with_tool_call(call_id=None, content=None))
        assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        self.assertIsNotNone(assistant["content"])

    def test_arguments_are_re_encoded_as_a_json_string(self) -> None:
        """The wire wants a string, not an object, and unknown fields must not leak."""
        msgs = self._second_call_messages(_reply_with_tool_call(call_id=None))
        assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        call = assistant["tool_calls"][0]
        self.assertEqual(call["type"], "function")
        self.assertIsInstance(call["function"]["arguments"], str)
        self.assertEqual(json.loads(call["function"]["arguments"])["name"], "oak side table")
        self.assertEqual(set(call.keys()), {"id", "type", "function"})

    def test_two_tool_calls_get_distinct_ids(self) -> None:
        two = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"function": {"name": "place_item", "arguments": "{}"}},
            {"function": {"name": "inspect_objects", "arguments": "{}"}},
        ]}}]}
        msgs = self._second_call_messages(two)
        assistant = [m for m in msgs if m.get("role") == "assistant"][-1]
        ids = [c["id"] for c in assistant["tool_calls"]]
        self.assertEqual(len(set(ids)), 2, f"ids must be distinct, got {ids}")

    def test_concurrent_rounds_do_not_reuse_ids(self) -> None:
        """A second round must not repeat round 1's synthesised ids."""
        seen: list[list[dict]] = []

        async def complete_fn(messages, tools_enabled):
            seen.append([dict(m) for m in messages])
            if len(seen) <= 2:
                return _reply_with_tool_call(call_id=None)
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

        async def execute_fn(name, arguments):
            return {"ok": True}

        asyncio.run(run_tool_loop(
            complete_fn=complete_fn,
            execute_fn=execute_fn,
            messages=[{"role": "user", "content": "go"}],
            max_rounds=4,
        ))
        all_ids = [
            c["id"]
            for m in seen[-1]
            if m.get("role") == "assistant" and m.get("tool_calls")
            for c in m["tool_calls"]
        ]
        self.assertEqual(len(all_ids), len(set(all_ids)), f"ids reused across rounds: {all_ids}")


if __name__ == "__main__":
    unittest.main()
