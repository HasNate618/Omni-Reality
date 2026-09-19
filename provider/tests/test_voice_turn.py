"""P0 voice turn: tool loop inside the planner, cloud PCM in speak.audio.

Offline only: fake complete_fn/execute_fn/synthesizer, no sockets, no credit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import unittest

from coordinator import turn
from coordinator.planner import YibuPlanner
from coordinator.session import CoordinatorState, UtteranceBuffer
from protocol.ids import new_ulid


def _tool_response(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            }
        }]
    }


def _text_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class PlannerToolLoopTests(unittest.TestCase):
    def test_loop_runs_tools_then_closes_tools_disabled(self) -> None:
        frame_id = new_ulid()
        flags: list[bool] = []
        ghost = {
            "kind": "ghost",
            "target": {"type": "image_point", "frame_id": frame_id, "u": 0.5, "v": 0.5},
            "motion": {"kind": "rotate", "axis": "y", "angle_deg": 30, "period_s": 2},
        }
        responses = [
            _tool_response("inspect_objects", {
                "frame_id": frame_id,
                "target": {"type": "image_point", "frame_id": frame_id, "u": 0.5, "v": 0.5},
            }),
            _tool_response("emit_scene_ops", {"ops": [ghost]}, call_id="call_2"),
            _tool_response("emit_scene_ops", {"ops": [ghost]}, call_id="call_3"),
            _tool_response("emit_scene_ops", {"ops": [ghost]}, call_id="call_4"),
            _text_response('{"heard": "mark it", "say": "There it is."}'),
        ]

        async def complete_fn(messages, tools_enabled):
            flags.append(tools_enabled)
            return responses[len(flags) - 1]

        async def execute_fn(name, arguments):
            self.assertEqual(name, "inspect_objects")
            return {"frame_id": frame_id, "count": 0, "objects": []}

        planner = YibuPlanner(complete_fn=complete_fn, execute_fn=execute_fn)
        plan = asyncio.run(planner.plan(
            pcm=b"\x00\x00" * 9000,
            jpeg=b"fakejpeg",
            envelope={"frame_id": frame_id, "stage_epoch": 0},
            context=[],
        ))
        self.assertEqual(flags, [True, True, True, True, False])
        self.assertEqual(len(plan.ops), 3)
        self.assertTrue(all(op["kind"] == "ghost" for op in plan.ops))
        self.assertEqual(plan.text, "There it is.")

    def test_loop_ending_on_text_needs_no_extra_call(self) -> None:
        frame_id = new_ulid()
        flags: list[bool] = []
        responses = [
            _text_response('{"heard": "hi", "say": "Hello."}'),
        ]

        async def complete_fn(messages, tools_enabled):
            flags.append(tools_enabled)
            return responses[len(flags) - 1]

        async def execute_fn(name, arguments):
            raise AssertionError("no tools should run")

        planner = YibuPlanner(complete_fn=complete_fn, execute_fn=execute_fn)
        plan = asyncio.run(planner.plan(
            pcm=b"\x00\x00" * 9000,
            jpeg=b"fakejpeg",
            envelope={"frame_id": frame_id, "stage_epoch": 0},
            context=[],
        ))
        self.assertEqual(flags, [True])
        self.assertEqual(plan.ops, [])
        self.assertEqual(plan.text, "Hello.")

    def test_second_place_procedural_drops(self) -> None:
        from coordinator.planner import accept_model_ops as planner_accept

        frame_id = new_ulid()
        proc = {
            "kind": "place_procedural",
            "target": {"type": "capture_hint", "frame_id": frame_id},
            "elements": [
                {"element": "cube", "color": "cyan", "size": "small", "material": "solid"}
            ],
        }
        ops = planner_accept([proc, dict(proc), {"kind": "undo"}], frame_id)
        self.assertEqual([op["kind"] for op in ops], ["place_procedural", "undo"])

    def test_default_wires_live_backend_lazily(self) -> None:
        planner = YibuPlanner()
        # Backend present but does no I/O until plan() runs.
        self.assertTrue(callable(planner._complete_fn))
        self.assertIsNone(planner._execute_fn)
        self.assertIsNone(planner._jobs)


def _pcm_buffer() -> UtteranceBuffer:
    buf = UtteranceBuffer()
    buf.pcm = bytearray(b"\x00\x01" * 9000)
    buf.jpeg = b"fakejpeg"
    buf.envelope = {"frame_id": new_ulid(), "stage_epoch": 0}
    return buf


class SpeakPcmTests(unittest.TestCase):
    def _run(self, planner_text="Hi there.", synth=None):
        from coordinator.planner import StubPlanner

        state = CoordinatorState(
            planner=StubPlanner(ops_factory=lambda env: [], text=planner_text)
        )
        state.synthesizer = synth
        sent: list[tuple] = []

        async def send(mtype, turn_id, payload, utterance_id):
            sent.append((mtype, payload))

        asyncio.run(turn._run_turn(state, send, 1, "u1", _pcm_buffer()))
        speaks = [p for t, p in sent if t == "speak"]
        self.assertEqual(len(speaks), 1)
        return speaks[0], state

    def test_speak_carries_cloud_pcm(self) -> None:
        pcm = b"\x01\x02" * 800

        async def synth(text):
            self.assertTrue(text)
            return pcm

        payload, _state = self._run(synth=synth)
        audio = payload["audio"]
        self.assertEqual(audio["encoding"], "pcm_s16le")
        self.assertEqual(audio["sample_rate"], 16000)
        self.assertEqual(audio["channels"], 1)
        self.assertEqual(base64.b64decode(audio["data_b64"]), pcm)

    def test_synth_failure_marks_voice_gate_failed(self) -> None:
        async def synth(text):
            raise RuntimeError("tts down")

        payload, state = self._run(synth=synth)
        self.assertIsNone(payload["audio"])
        self.assertEqual(state.context[-1].get("voice_gate"), "failed")

    def test_no_synthesizer_keeps_audio_none(self) -> None:
        payload, state = self._run(synth=None)
        self.assertIsNone(payload["audio"])
        self.assertEqual(state.context[-1].get("voice_gate"), "degraded")


if __name__ == "__main__":
    unittest.main()
