"""Headset-chosen mode: `hello.payload.mode` decides this connection's planner.

The launcher runs on the headset, so the laptop cannot know the mode until the
first message arrives. These cover the routing and, importantly, that an older
headset build that sends no mode still works exactly as before.
"""

from __future__ import annotations

import unittest

from coordinator.server import HEADSET_MODES, CoordinatorState, apply_headset_mode


class Socket:
    async def send(self, raw):
        return None


def configure(mode, **kwargs):
    state = CoordinatorState(planner=None)
    options = {"planner_kind": "stub", "model": None, "sam2_url": None,
               "voice_only": False, "perception_qa": False}
    options.update(kwargs)
    applied = apply_headset_mode(state, Socket(), mode, **options)
    return state, applied


class ModeRoutingTests(unittest.TestCase):
    def test_layout_builds_the_layout_planner(self) -> None:
        state, applied = configure("layout")
        self.assertEqual(applied, "layout")
        self.assertEqual(type(state.planner).__name__, "LayoutPlanner")
        self.assertTrue(state.planner.layout)

    def test_layout_never_attaches_a_tracker(self) -> None:
        """SAM 2 would swallow the turn, and layout does not need it."""
        state, _ = configure("layout", sam2_url="ws://127.0.0.1:8766")
        self.assertIsNone(state.tracking)

    def test_layout_speaks_locally(self) -> None:
        state, _ = configure("layout")
        self.assertIsNotNone(state.synthesizer)

    def test_tracking_attaches_the_tracker(self) -> None:
        state, applied = configure("tracking", sam2_url="ws://127.0.0.1:8766")
        self.assertEqual(applied, "tracking")
        self.assertIsNotNone(state.tracking)
        self.assertFalse(getattr(state.planner, "layout", False))

    def test_tutorial_is_the_same_configuration_as_tracking(self) -> None:
        """The guide is a branch inside the tracking planner, not a mode."""
        tracking, _ = configure("tracking", sam2_url="ws://127.0.0.1:8766")
        tutorial, applied = configure("tutorial", sam2_url="ws://127.0.0.1:8766")
        self.assertEqual(applied, "tutorial")
        self.assertEqual(type(tutorial.planner).__name__, type(tracking.planner).__name__)
        self.assertIsNotNone(tutorial.tracking)

    def test_no_mode_leaves_the_cli_choice_alone(self) -> None:
        """An older headset build sends no mode; nothing should change."""
        state, applied = configure(None)
        self.assertIsNone(applied)
        self.assertIsNone(state.planner)

    def test_unknown_mode_is_ignored_not_obeyed(self) -> None:
        for bad in ("hovercraft", "", 7, ["layout"]):
            state, applied = configure(bad)
            self.assertIsNone(applied, bad)
            self.assertIsNone(state.planner, bad)

    def test_every_advertised_mode_is_handled(self) -> None:
        for mode in HEADSET_MODES:
            _state, applied = configure(mode, sam2_url="ws://127.0.0.1:8766")
            self.assertEqual(applied, mode)

    def test_switching_mode_replaces_the_planner(self) -> None:
        """A reconnect re-runs this, so the second choice must fully win."""
        state = CoordinatorState(planner=None)
        opts = {"planner_kind": "stub", "model": None,
                "sam2_url": "ws://127.0.0.1:8766", "voice_only": False,
                "perception_qa": False}
        apply_headset_mode(state, Socket(), "tracking", **opts)
        self.assertIsNotNone(state.tracking)
        apply_headset_mode(state, Socket(), "layout", **opts)
        self.assertEqual(type(state.planner).__name__, "LayoutPlanner")
        self.assertIsNone(state.tracking, "layout must drop the tracker")


if __name__ == "__main__":
    unittest.main()
