from __future__ import annotations

import asyncio
import json
import unittest

from omni.reasoner import require_omni_inputs, run_tool_loop
from protocol.validate import load_fixture


class ReasonerTests(unittest.TestCase):
    def test_transcript_only_fails_gate(self) -> None:
        self.assertIsNotNone(require_omni_inputs(b"\x00\x00", None))
        pcm = b"\x00\x00" * 8000
        self.assertIsNotNone(require_omni_inputs(pcm, None))
        self.assertIsNone(require_omni_inputs(pcm, "qq=="))

    def test_loop_stops_after_four_tool_rounds(self) -> None:
        flags = []

        async def complete_fn(messages, tools_enabled):
            flags.append(tools_enabled)
            return {"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "c",
                    "type": "function",
                    "function": {"name": "inspect_objects", "arguments": "{}"},
                }],
            }}]}

        async def execute_fn(name, arguments):
            return {"count": 0, "objects": [], "frame_id": "01k00000000000000000000001", "error": "no_object"}

        ops, _text = asyncio.run(run_tool_loop(
            complete_fn=complete_fn, execute_fn=execute_fn, messages=[], max_rounds=4,
        ))
        self.assertEqual(flags, [True, True, True, True])
        self.assertEqual(ops, [])

    def test_emit_scene_ops_collected(self) -> None:
        ghost = {
            "kind": "ghost",
            "target": load_fixture("valid", "scene_op_mark.json")["target"],
            "motion": {"kind": "rotate", "axis": "y", "angle_deg": 30, "period_s": 2},
        }

        async def complete_fn(messages, tools_enabled):
            return {"choices": [{"message": {"tool_calls": [{
                "id": "1",
                "type": "function",
                "function": {"name": "emit_scene_ops", "arguments": json.dumps({"ops": [ghost]})},
            }]}}]}

        async def execute_fn(name, arguments):
            return {"ok": True}

        ops, _ = asyncio.run(run_tool_loop(
            complete_fn=complete_fn, execute_fn=execute_fn, messages=[],
        ))
        self.assertEqual(ops[0]["kind"], "ghost")
