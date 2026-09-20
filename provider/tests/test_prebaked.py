from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from coordinator.prebaked import load_registry, lookup


class LoadRegistryTests(unittest.TestCase):
    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(load_registry(Path("/nonexistent/prebaked.json")), {})

    def test_loads_name_to_artifact_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prebaked.json"
            path.write_text(json.dumps({
                "oak side table": "01m2xbae3n81b4scq0k83teqjw",
            }))
            registry = load_registry(path)
            self.assertEqual(lookup(registry, "Oak Side Table"), "01m2xbae3n81b4scq0k83teqjw")

    def test_malformed_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prebaked.json"
            path.write_text("{ not json")
            self.assertEqual(load_registry(path), {})

    def test_non_string_values_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prebaked.json"
            path.write_text(json.dumps({"table": 12, "lamp": "01m2xbae3n81b4scq0k83teqjw"}))
            registry = load_registry(path)
            self.assertIsNone(lookup(registry, "table"))
            self.assertEqual(lookup(registry, "lamp"), "01m2xbae3n81b4scq0k83teqjw")

    def test_unknown_name_is_none(self) -> None:
        self.assertIsNone(lookup({}, "nothing here"))

    def test_non_dict_top_level_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prebaked.json"
            path.write_text(json.dumps(["oak side table"]))
            self.assertEqual(load_registry(path), {})

    def test_missing_file_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prebaked.json"
            self.assertEqual(load_registry(path), {})
            self.assertFalse(path.exists())
