from __future__ import annotations

import unittest

from coordinator.layout import pack_offsets, run_length


class PackOffsetsTests(unittest.TestCase):
    def test_single_item_is_centred_on_the_hit(self) -> None:
        # A lone placement must land where the wearer pointed, so the first
        # item's offset is zero rather than half its own width.
        self.assertEqual(pack_offsets([[0.55, 0.40, 0.72]]), [0.0])

    def test_second_item_clears_the_first_width_plus_gap(self) -> None:
        offsets = pack_offsets([[0.55, 0.40, 0.72], [0.40, 0.40, 0.50]])
        self.assertAlmostEqual(offsets[0], 0.0, places=6)
        self.assertAlmostEqual(offsets[1], 0.55 / 2 + 0.05 + 0.40 / 2, places=6)

    def test_order_is_stable_and_matches_input(self) -> None:
        extents = [[1.00, 0.30, 0.40], [0.20, 0.30, 0.40], [0.60, 0.30, 0.40]]
        offsets = pack_offsets(extents)
        self.assertAlmostEqual(offsets[0], 0.0, places=6)
        self.assertAlmostEqual(offsets[1], 0.50 + 0.05 + 0.10, places=6)
        self.assertAlmostEqual(offsets[2], 0.50 + 0.05 + 0.20 + 0.05 + 0.30, places=6)

    def test_offsets_are_monotonic(self) -> None:
        offsets = pack_offsets([[0.4, 0.4, 0.5], [0.4, 0.4, 0.5], [0.4, 0.4, 0.5]])
        self.assertEqual(offsets, sorted(offsets))

    def test_empty_is_empty(self) -> None:
        self.assertEqual(pack_offsets([]), [])

    def test_positions_never_overlap(self) -> None:
        extents = [[0.55, 0.40, 0.72], [0.40, 0.40, 0.50], [0.30, 0.30, 1.50]]
        offsets = pack_offsets(extents)
        for index in range(1, len(extents)):
            left_edge = offsets[index] - extents[index][0] / 2
            right_edge = offsets[index - 1] + extents[index - 1][0] / 2
            self.assertGreaterEqual(left_edge - right_edge, 0.05 - 1e-9)

    def test_gap_parameter_is_honoured(self) -> None:
        tight = pack_offsets([[0.4, 0.4, 0.5], [0.4, 0.4, 0.5]], gap=0.0)
        loose = pack_offsets([[0.4, 0.4, 0.5], [0.4, 0.4, 0.5]], gap=0.5)
        self.assertAlmostEqual(loose[1] - tight[1], 0.5, places=6)


class RunLengthTests(unittest.TestCase):
    def test_single_item_run_is_its_width(self) -> None:
        self.assertAlmostEqual(run_length([[0.55, 0.40, 0.72]]), 0.55, places=6)

    def test_run_adds_widths_and_gaps(self) -> None:
        self.assertAlmostEqual(
            run_length([[0.55, 0.40, 0.72], [0.40, 0.40, 0.50], [0.30, 0.30, 1.50]]),
            0.55 + 0.40 + 0.30 + 2 * 0.05,
            places=6,
        )

    def test_empty_run_is_zero(self) -> None:
        self.assertAlmostEqual(run_length([]), 0.0, places=6)
