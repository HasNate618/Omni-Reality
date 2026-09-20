from __future__ import annotations

import unittest

from coordinator.listings import ListingMemory, normalize_name


class NormalizeNameTests(unittest.TestCase):
    def test_folds_case_and_collapses_space(self) -> None:
        self.assertEqual(normalize_name("  Oak   Side Table "), "oak side table")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(normalize_name(""), "")


class ListingMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = ListingMemory()

    def test_record_then_get(self) -> None:
        self.memory.record(
            "oak side table", [0.55, 0.40, 0.72], "01k5j8g0008q3m7b2d6h9n4r5v", "page"
        )
        row = self.memory.get("Oak Side Table")
        self.assertIsNotNone(row)
        self.assertEqual(row["extent_m"], [0.55, 0.40, 0.72])
        self.assertEqual(row["source"], "page")
        self.assertEqual(row["source_frame_id"], "01k5j8g0008q3m7b2d6h9n4r5v")

    def test_same_name_updates_one_row(self) -> None:
        self.memory.record("oak side table", [0.55, 0.40, 0.72], "f1", "page")
        self.memory.record("Oak Side Table", [0.60, 0.40, 0.72], "f2", "spoken")
        self.assertEqual(len(self.memory.rows()), 1)
        self.assertEqual(self.memory.get("oak side table")["extent_m"], [0.60, 0.40, 0.72])
        self.assertEqual(self.memory.get("oak side table")["source"], "spoken")

    def test_distinct_names_keep_distinct_rows(self) -> None:
        self.memory.record("table", [0.55, 0.40, 0.72], "f1", "page")
        self.memory.record("lamp", [0.30, 0.30, 1.50], "f1", "page")
        self.assertEqual(len(self.memory.rows()), 2)

    def test_unknown_name_is_none(self) -> None:
        self.assertIsNone(self.memory.get("nothing here"))

    def test_clear_empties(self) -> None:
        self.memory.record("table", [0.55, 0.40, 0.72], "f1", "page")
        self.memory.clear()
        self.assertEqual(self.memory.rows(), [])

    def test_stores_copies_not_caller_list(self) -> None:
        extents = [0.55, 0.40, 0.72]
        self.memory.record("table", extents, "f1", "page")
        extents[0] = 9.99
        self.assertEqual(self.memory.get("table")["extent_m"], [0.55, 0.40, 0.72])
