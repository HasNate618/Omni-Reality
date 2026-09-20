# Generated furniture at listed scale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A size stated on a page becomes a life-size grabbable object in the room, with the generated mesh fitted inside that box instead of squeezed into a 1 m sphere.

**Architecture:** The model states a listing's own dimensions through a new `place_listing` tool. The coordinator validates the metres, records the row, and authors a `place_generated` op carrying `extent_m`. Quest plants a listed-size AABB on the existing capture-time hit, ACKs `placed` immediately, then fetches the GLB with a bounded retry and fits it uniformly inside the box. Both the box and the mesh live under one `drawing_id` in `DrawingStore` so remove, undo, revise, and grab all address one object.

**Tech Stack:** Python 3 (`unittest`, `jsonschema`), Unity 6 LTS `6000.6.2f1` (C#, NUnit EditMode, glTFast, XR Interaction Toolkit 3.6.1, Meta XR 205).

**Binding design:** `docs/superpowers/specs/2026-09-20-generated-furniture-scale-design.md`. If this plan and that spec disagree, stop and fix the plan; do not invent a third contract.

## Global Constraints

Every task's requirements implicitly include this section.

- `extent_m` is exactly 3 numbers, each `>= 0.05` and `<= 3.0`. No other lengths, no non-numbers, no negatives.
- Packing gap between items is `0.05` m.
- Mesh aspect ratio differing from the listing box by more than 25% on any axis keeps the box and takes the approximate path.
- GLB fetch window: 6 attempts at ~15 s spacing.
- `MaxGlbBytes` is `25 * 1024 * 1024`; GLB magic is `glTF`.
- `DrawingStore.MaxDrawings` is `8`; eviction drops the oldest.
- `MAX_OPS_PER_TURN` is `3`.
- Listing `name` is 1–40 characters.
- World frame is `openxr_floor_stage`: Unity left-handed, `+X` right, `+Y` up, `+Z` forward, metres, quaternions `xyzw`.
- Canonical camera is **left**.
- The model never emits world coordinates, poses, quaternions, C#, URLs, or file paths, and never emits `place_generated` or `place_known`.
- `YIBU_API_KEY` lives only in the laptop environment. Never in code, logs, Unity, docs, or the repo.
- No cost arithmetic anywhere in the tooling.
- Python tests: `cd provider && . .venv/bin/activate && python -m unittest discover -s tests -v`. Tests are `unittest`, never `pytest`.
- Unity EditMode tests: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter <Filter> -testResults /tmp/<name>.xml -logFile /tmp/<name>.log'`
- Unity assemblies: production code lives in `Omni.Spatial` (`QuestDemo/Assets/Spatial/**`), tests in `Omni.Spatial.Editor.Tests` (`QuestDemo/Assets/Tests/Editor/**`). A new Unity package reference must be added to `QuestDemo/Assets/Spatial/Omni.Spatial.asmdef` or the code will not compile.
- Do not "fix" vendored example files to satisfy linters without checking upstream first.

---

## Findings that shaped this plan

Three things the spec did not know, found while reading the seams. Each forces a task below.

1. **`provider/coordinator/artifacts.py` serves a GLB only when `coordinator_may_place` is true** — job exists and `status == "ready"`. A box planted at accept time therefore 404s until the mesh bakes. The bounded retry in Task 9 is load-bearing, not defensive.
2. **`mark_ready` and `on_job_terminal` have no production caller.** The generation-to-placement seam is tested but unwired. Without Task 11, live generation never places anything, silently.
3. **`_session_generation_busy` allows one worker job at a time.** A demo that places three listings cannot generate three meshes concurrently. Task 6 adds a pre-baked artifact registry so the demo path needs no worker at all.

## File Structure

**Create**

| Path | Responsibility |
|---|---|
| `provider/coordinator/listings.py` | Listing memory (rows keyed by normalized name) and the `place_listing` handler |
| `provider/coordinator/layout.py` | Pure pack arithmetic: offsets and run length |
| `provider/coordinator/prebaked.py` | Load and query the pre-baked artifact registry |
| `provider/tests/test_listings.py` | Listing memory + handler tests |
| `provider/tests/test_layout.py` | Pack arithmetic tests |
| `provider/tests/test_prebaked.py` | Registry tests |
| `provider/protocol/fixtures/valid/scene_op_place_generated_extent.json` | Fixture for a sized gen op |
| `QuestDemo/Assets/Spatial/ListingBox.cs` | AABB fit math (pure) + the listed-box visual |
| `QuestDemo/Assets/Spatial/FloorPlaneGrab.cs` | XZ-only floor drag, Y and scale locked |
| `QuestDemo/Assets/Tests/Editor/ListingBoxTests.cs` | Fit math tests |
| `QuestDemo/Assets/Tests/Editor/FloorPlaneGrabTests.cs` | Constraint math tests |

**Modify**

| Path | Change |
|---|---|
| `provider/protocol/schemas/scene_op.json` | Add `extent_m` |
| `provider/coordinator/session.py` | `CoordinatorState.listings` |
| `provider/coordinator/jobs.py` | Job record carries `extent_m` and `planted` |
| `provider/omni/tools.py` | Declare `place_listing` |
| `provider/coordinator/planner.py` | Dispatch `place_listing`, carry coordinator ops |
| `provider/coordinator/turn.py` | `build_place_generated` takes `extent_m`; skip terminal emit when planted |
| `provider/coordinator/server.py` | Wire the job-ready poller to `on_job_terminal` |
| `provider/tests/test_protocol.py` | `extent_m` schema tests |
| `provider/tests/test_turn_tools.py` | Planted-job tests |
| `provider/tests/test_omni_tools.py` | Tool declaration test |
| `QuestDemo/Assets/Spatial/ProtocolJson.cs` | Parse `extent_m` |
| `QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs` | AABB fit, store registration, no placeholder cube |
| `QuestDemo/Assets/Spatial/DrawingStore.cs` | Generated records + revise |
| `QuestDemo/Assets/Spatial/CoordinatorClient.cs` | Pass the store to the placer |
| `QuestDemo/Assets/Spatial/Omni.Spatial.asmdef` | Reference XR Interaction Toolkit |
| `QuestDemo/Assets/Tests/Editor/GeneratedMeshPlacerTests.cs` | AABB + fallback tests |

---

## Task 1: `extent_m` on the scene_op schema

**Files:**
- Modify: `provider/protocol/schemas/scene_op.json`
- Create: `provider/protocol/fixtures/valid/scene_op_place_generated_extent.json`
- Test: `provider/tests/test_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the wire fields `extent_m: [number, number, number]` and `offset_m: number` on `scene_op`, consumed by Tasks 5, 6, 7, 8, 10.

- [ ] **Step 1: Write the failing tests**

Append to `provider/tests/test_protocol.py`, inside `class SchemaTests`:

```python
    def test_valid_place_generated_with_extent(self) -> None:
        data = load_fixture("valid", "scene_op_place_generated_extent.json")
        validate_instance("scene_op", data)

    def test_extent_must_be_exactly_three_numbers(self) -> None:
        data = load_fixture("valid", "scene_op_place_generated_extent.json")
        for bad in ([0.55, 0.40], [0.55, 0.40, 0.72, 0.10], [0.55, 0.40, "tall"]):
            data["extent_m"] = bad
            with self.assertRaises(ValidationError):
                validate_instance("scene_op", data)

    def test_extent_axes_out_of_range_rejected(self) -> None:
        data = load_fixture("valid", "scene_op_place_generated_extent.json")
        for bad in ([0.04, 0.40, 0.72], [0.55, 0.40, 3.01], [-0.55, 0.40, 0.72]):
            data["extent_m"] = bad
            with self.assertRaises(ValidationError):
                validate_instance("scene_op", data)

    def test_model_cannot_smuggle_extents_into_a_valid_op(self) -> None:
        # A valid model op with extent_m bolted on must be refused:
        # model_scene_op is additionalProperties:false, and that is what keeps
        # sizes off the model's wire.
        data = load_fixture("valid", "scene_op_procedural.json")
        data["extent_m"] = [0.55, 0.40, 0.72]
        with self.assertRaises(ValidationError):
            validate_instance("model_scene_op", data)

    def test_place_generated_is_not_a_model_kind(self) -> None:
        data = load_fixture("valid", "scene_op_place_generated_extent.json")
        with self.assertRaises(ValidationError):
            validate_instance("model_scene_op", data)

    def test_offset_is_optional_and_bounded(self) -> None:
        data = load_fixture("valid", "scene_op_place_generated_extent.json")
        data.pop("offset_m", None)
        validate_instance("scene_op", data)
        for bad in (-3.01, 3.01):
            data["offset_m"] = bad
            with self.assertRaises(ValidationError):
                validate_instance("scene_op", data)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_protocol.SchemaTests -v`
Expected: FAIL — `FileNotFoundError` for `scene_op_place_generated_extent.json`.

- [ ] **Step 3: Add the fixture**

Create `provider/protocol/fixtures/valid/scene_op_place_generated_extent.json`:

```json
{
  "op_id": "01k5j8g0019q3m7b2d6h9n4r5v",
  "turn_id": 4,
  "stage_epoch": 1,
  "kind": "place_generated",
  "drawing_id": null,
  "job_id": "01m2xbae3n81b4scq0k83teqjw",
  "extent_m": [0.55, 0.40, 0.72],
  "offset_m": 0.0,
  "target": { "type": "capture_hint", "frame_id": "01k5j8g0008q3m7b2d6h9n4r5v" }
}
```

- [ ] **Step 4: Add the schema property**

In `provider/protocol/schemas/scene_op.json`, in `properties`, immediately after the `"job_id"` line, add:

```json
    "extent_m": {
      "type": "array",
      "minItems": 3,
      "maxItems": 3,
      "items": { "type": "number", "minimum": 0.05, "maximum": 3.0 }
    },
    "offset_m": { "type": "number", "minimum": -3.0, "maximum": 3.0 },
```

`offset_m` is the frame-relative distance along the width axis that this item's centre sits from the first item's centre (Task 3, Task 6). It is the same category as `extent_m` — metres in a local frame, never a world coordinate — which is why it can be on the wire while `world_point` cannot.

Remember the trailing comma on the preceding `"job_id"` line. Do **not** touch `model_scene_op.json`: `additionalProperties: false` there is what keeps the model from emitting extents.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_protocol -v`
Expected: PASS, including the six new tests and every pre-existing schema test.

- [ ] **Step 6: Commit**

```bash
git add provider/protocol/schemas/scene_op.json provider/protocol/fixtures/valid/scene_op_place_generated_extent.json provider/tests/test_protocol.py
git commit -m "Protocol: place_generated carries extent_m"
```

---

## Task 2: Listing memory

**Files:**
- Create: `provider/coordinator/listings.py`
- Test: `provider/tests/test_listings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ListingMemory` with `record(name, extent_m, source_frame_id, source) -> dict`, `get(name) -> dict | None`, `rows() -> list[dict]`, `clear() -> None`; and `normalize_name(name) -> str`. Consumed by Tasks 6 and 7.

- [ ] **Step 1: Write the failing test**

Create `provider/tests/test_listings.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_listings -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.listings'`.

- [ ] **Step 3: Write the implementation**

Create `provider/coordinator/listings.py`:

```python
"""Listing memory (spec §4): sizes the model stated, keyed by name.

Rows are recorded when a listing is placed, never on a guess. A row with no
stated sizes is not a row this store can hold, so every row is placeable and
the honesty gate lives in the caller's validation, not here.
"""

from __future__ import annotations

from typing import Any


def normalize_name(name: str) -> str:
    """Fold case and collapse whitespace so repeats update one row."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split()).strip().lower()


class ListingMemory:
    """Session-scoped listing rows keyed by normalized name."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def record(
        self,
        name: str,
        extent_m: list[float],
        source_frame_id: str | None,
        source: str,
    ) -> dict[str, Any]:
        key = normalize_name(name)
        row = {
            "name": " ".join(name.split()) if isinstance(name, str) else "",
            "extent_m": [float(axis) for axis in extent_m],
            "source_frame_id": source_frame_id,
            "source": source,
        }
        self._rows[key] = row
        return row

    def get(self, name: str) -> dict[str, Any] | None:
        return self._rows.get(normalize_name(name))

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows.values())

    def clear(self) -> None:
        self._rows.clear()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_listings -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add provider/coordinator/listings.py provider/tests/test_listings.py
git commit -m "Coordinator: listing memory keyed by normalized name"
```

---

## Task 3: Pack arithmetic

**Files:**
- Create: `provider/coordinator/layout.py`
- Test: `provider/tests/test_layout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `pack_offsets(extents, gap=0.05) -> list[float]` and `run_length(extents, gap=0.05) -> float`. Consumed by Task 6 (per-item `offset_m`) and by the demo's spoken run length.

- [ ] **Step 1: Write the failing test**

Create `provider/tests/test_layout.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_layout -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.layout'`.

- [ ] **Step 3: Write the implementation**

Create `provider/coordinator/layout.py`:

```python
"""Pack arithmetic for a row of placed listings (spec §7.1).

Pure functions over listing extents. They never see world coordinates: the
result is a distance along the placement frame's width axis, which Quest
applies. The model never receives these numbers as world metres.
"""

from __future__ import annotations

GAP_M = 0.05


def pack_offsets(extents: list[list[float]], gap: float = GAP_M) -> list[float]:
    """Centre offset along the width axis for each item, in input order.

    Offsets are relative to the first item's centre, which is the hit. A lone
    placement therefore lands where the wearer pointed instead of half its own
    width to the side. Each later item clears the running width plus one gap.
    """
    if not extents:
        return []
    offsets = [0.0]
    # The first item is centred on the hit, so the running edge starts at half
    # its width, not its full width.
    cursor = float(extents[0][0]) / 2.0
    for extent in extents[1:]:
        width = float(extent[0])
        offsets.append(cursor + gap + width / 2.0)
        cursor += width / 2.0 + gap + width / 2.0
    return offsets


def run_length(extents: list[list[float]], gap: float = GAP_M) -> float:
    """Total width of the packed row, gaps included. Empty is 0.0."""
    if not extents:
        return 0.0
    widths = sum(float(extent[0]) for extent in extents)
    return widths + gap * (len(extents) - 1)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_layout -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add provider/coordinator/layout.py provider/tests/test_layout.py
git commit -m "Coordinator: pack arithmetic for a row of listings"
```

---

## Task 4: Pre-baked artifact registry

**Files:**
- Create: `provider/coordinator/prebaked.py`
- Test: `provider/tests/test_prebaked.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_registry(path) -> dict[str, str]` and `lookup(registry, name) -> str | None`. Consumed by Task 6.

**Why:** `_session_generation_busy` allows one worker job at a time (`provider/coordinator/jobs.py`), so a three-item demo cannot generate concurrently. A pre-baked listing resolves to an artifact that already exists and needs no worker.

- [ ] **Step 1: Write the failing test**

Create `provider/tests/test_prebaked.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_prebaked -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coordinator.prebaked'`.

- [ ] **Step 3: Write the implementation**

Create `provider/coordinator/prebaked.py`:

```python
"""Pre-baked artifact registry (spec §5.3).

A listing name that maps to an artifact id is placeable without queueing a
worker. This exists because generation is serialized by
``_session_generation_busy``: a demonstration that places several listings
cannot generate them concurrently.

Whether a listing is pre-baked is coordinator configuration, never a model
input, and the registry deliberately does not carry extents — the model still
has to state sizes, so the honesty gate is unchanged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from coordinator.listings import normalize_name

logger = logging.getLogger(__name__)

Registry = dict[str, str]


def load_registry(path: Path) -> Registry:
    """Read a name → artifact id map. Any problem yields an empty registry."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    registry: Registry = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            registry[normalize_name(key)] = value
    return registry


def lookup(registry: Registry, name: str) -> str | None:
    """Artifact id configured for this listing name, or None."""
    return registry.get(normalize_name(name))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_prebaked -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add provider/coordinator/prebaked.py provider/tests/test_prebaked.py
git commit -m "Coordinator: pre-baked artifact registry"
```

---

## Task 5: Declare the `place_listing` tool

**Files:**
- Modify: `provider/omni/tools.py`
- Test: `provider/tests/test_omni_tools.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `place_listing` entry in `TOOL_DEFINITIONS` requiring `name`, `extent_m`, `target`. Consumed by Task 6.

**Note:** `extent_m` is **required**. That is the honesty gate: the model cannot place a listing without stating sizes, so a listing it cannot size is a listing it must ask about.

- [ ] **Step 1: Write the failing test**

Open `provider/tests/test_omni_tools.py` and append:

```python
class PlaceListingToolTests(unittest.TestCase):
    def _tool(self) -> dict:
        for entry in TOOL_DEFINITIONS:
            if entry["function"]["name"] == "place_listing":
                return entry["function"]
        self.fail("place_listing not declared")

    def test_declared_with_required_arguments(self) -> None:
        tool = self._tool()
        self.assertEqual(
            sorted(tool["parameters"]["required"]),
            ["extent_m", "name", "target"],
        )

    def test_extent_m_is_three_bounded_numbers(self) -> None:
        schema = self._tool()["parameters"]["properties"]["extent_m"]
        self.assertEqual(schema["minItems"], 3)
        self.assertEqual(schema["maxItems"], 3)
        self.assertEqual(schema["items"]["minimum"], 0.05)
        self.assertEqual(schema["items"]["maximum"], 3.0)

    def test_name_is_bounded(self) -> None:
        schema = self._tool()["parameters"]["properties"]["name"]
        self.assertEqual(schema["maxLength"], 40)

    def test_no_property_accepts_a_world_point(self) -> None:
        tool = self._tool()
        self.assertNotIn("world_point", json.dumps(tool))
        self.assertNotIn("px", tool["parameters"]["properties"])
```

Confirm the file already imports `json` and `TOOL_DEFINITIONS`; if `json` is missing, add `import json` to the imports, and ensure `from omni.tools import TOOL_DEFINITIONS` is present.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_omni_tools.PlaceListingToolTests -v`
Expected: FAIL — `AssertionError: place_listing not declared`.

- [ ] **Step 3: Declare the tool**

In `provider/omni/tools.py`, insert this entry into `TOOL_DEFINITIONS` after the `start_generation` entry:

```python
    {
        "type": "function",
        "function": {
            "name": "place_listing",
            "description": (
                "Place a product you have sized onto a surface in the room. "
                "State the sizes you read from the page; never guess them. "
                "If you do not have all three sizes, ask the wearer instead."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "extent_m", "target"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 40},
                    "extent_m": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number", "minimum": 0.05, "maximum": 3.0},
                    },
                    "target": {"type": "object"},
                },
            },
        },
    },
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_omni_tools -v`
Expected: PASS, including every pre-existing tool test.

- [ ] **Step 5: Commit**

```bash
git add provider/omni/tools.py provider/tests/test_omni_tools.py
git commit -m "Omni tool: place_listing requires stated sizes"
```

---

## Task 6: `place_listing` handler and coordinator ops

**Files:**
- Modify: `provider/coordinator/listings.py`
- Modify: `provider/coordinator/planner.py`
- Modify: `provider/coordinator/session.py`
- Modify: `provider/coordinator/jobs.py`
- Test: `provider/tests/test_listings.py`

**Interfaces:**
- Consumes: `ListingMemory` (Task 2), `pack_offsets`/`run_length` (Task 3), `load_registry`/`lookup` (Task 4), the `place_listing` declaration (Task 5).
- Produces: `handle_place_listing(store, *, listings, args, current_frame_id, prebaked, queue_fn=None) -> tuple[dict, dict | None]` returning `(tool_result, coordinator_op)`. `YibuPlanner.bind_tools` gains `listings` and `prebaked`. Consumed by Task 7.

**Why the op is carried, not sent:** `_dispatch_tool` runs inside `plan()` and has no turn id or stage epoch. It returns a partial op; `_run_turn` completes and sends it in Task 7.

- [ ] **Step 1: Write the failing tests**

Append to `provider/tests/test_listings.py`:

```python
import asyncio

from coordinator.jobs import JobStore
from coordinator.listings import handle_place_listing

_FRAME = "01k5j8g0008q3m7b2d6h9n4r5v"
_TARGET = {"type": "capture_hint", "frame_id": _FRAME}
_ARTIFACT = "01m2xbae3n81b4scq0k83teqjw"


class HandlePlaceListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = JobStore()
        self.memory = ListingMemory()

    def _call(self, args, *, prebaked=None, current_frame_id=_FRAME):
        return asyncio.run(handle_place_listing(
            self.store,
            listings=self.memory,
            args=args,
            current_frame_id=current_frame_id,
            prebaked=prebaked or {},
        ))

    def _good(self) -> dict:
        return {"name": "oak side table", "extent_m": [0.55, 0.40, 0.72], "target": _TARGET}

    def test_valid_args_return_an_op(self) -> None:
        result, op = self._call(self._good())
        self.assertNotIn("error", result)
        self.assertEqual(op["kind"], "place_generated")
        self.assertEqual(op["extent_m"], [0.55, 0.40, 0.72])
        self.assertEqual(op["target"], _TARGET)

    def test_valid_args_record_a_row(self) -> None:
        self._call(self._good())
        row = self.memory.get("oak side table")
        self.assertEqual(row["extent_m"], [0.55, 0.40, 0.72])
        self.assertEqual(row["source"], "page")
        self.assertEqual(row["source_frame_id"], _FRAME)

    def test_missing_extent_m_refused(self) -> None:
        result, op = self._call({"name": "table", "target": _TARGET})
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)
        self.assertEqual(self.memory.rows(), [])

    def test_wrong_axis_count_refused(self) -> None:
        args = self._good()
        args["extent_m"] = [0.55, 0.40]
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_out_of_range_axis_refused(self) -> None:
        for bad in (0.04, 3.01, -0.5):
            args = self._good()
            args["extent_m"] = [bad, 0.40, 0.72]
            result, op = self._call(args)
            self.assertEqual(result["error"], "invalid", f"axis {bad} should refuse")
            self.assertIsNone(op)

    def test_non_numeric_axis_refused(self) -> None:
        args = self._good()
        args["extent_m"] = [0.55, 0.40, "tall"]
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_empty_name_refused(self) -> None:
        args = self._good()
        args["name"] = "   "
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_overlong_name_refused(self) -> None:
        args = self._good()
        args["name"] = "x" * 41
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_no_current_frame_refused(self) -> None:
        result, op = self._call(self._good(), current_frame_id=None)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_stale_frame_target_refused(self) -> None:
        args = self._good()
        args["target"] = {"type": "capture_hint", "frame_id": "01k5j8g0008q3m7b2d6h9n4r5w"}
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_world_point_target_refused(self) -> None:
        args = self._good()
        args["target"] = {"type": "world_point", "px": 1.0, "py": 1.0, "pz": 1.0,
                          "frame": "openxr_floor_stage"}
        result, op = self._call(args)
        self.assertEqual(result["error"], "invalid")
        self.assertIsNone(op)

    def test_live_path_queues_a_job(self) -> None:
        result, op = self._call(self._good())
        job = self.store.jobs[op["job_id"]]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["extent_m"], [0.55, 0.40, 0.72])
        self.assertTrue(job["planted"])

    def test_prebaked_path_creates_a_ready_job_with_no_worker(self) -> None:
        queued = []

        async def queue_fn(**kwargs):
            queued.append(kwargs)
            return {"status": "queued"}

        result, op = asyncio.run(handle_place_listing(
            self.store,
            listings=self.memory,
            args=self._good(),
            current_frame_id=_FRAME,
            prebaked={"oak side table": _ARTIFACT},
            queue_fn=queue_fn,
        ))
        self.assertNotIn("error", result)
        self.assertEqual(op["job_id"], _ARTIFACT)
        self.assertEqual(self.store.jobs[_ARTIFACT]["status"], "ready")
        self.assertTrue(self.store.jobs[_ARTIFACT]["planted"])
        self.assertEqual(queued, [])

    def test_repeat_placement_updates_one_row(self) -> None:
        self._call(self._good())
        args = self._good()
        args["extent_m"] = [0.60, 0.40, 0.72]
        self._call(args)
        self.assertEqual(len(self.memory.rows()), 1)
        self.assertEqual(self.memory.get("oak side table")["extent_m"], [0.60, 0.40, 0.72])

    def test_first_item_sits_at_the_hit(self) -> None:
        _, op = self._call(self._good())
        self.assertAlmostEqual(op["offset_m"], 0.0, places=6)

    def test_second_item_clears_the_first(self) -> None:
        self._call(self._good())
        lamp = {"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET}
        _, op = self._call(lamp)
        # first width 0.55 half + 0.05 gap + second width 0.30 half
        self.assertAlmostEqual(op["offset_m"], 0.275 + 0.05 + 0.15, places=6)

    def test_repeat_placement_keeps_its_slot(self) -> None:
        self._call(self._good())
        self._call({"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET})
        _, op = self._call(self._good())
        self.assertAlmostEqual(op["offset_m"], 0.0, places=6)

    def test_result_reports_the_packed_run_length(self) -> None:
        self._call(self._good())
        lamp = {"name": "floor lamp", "extent_m": [0.30, 0.30, 1.50], "target": _TARGET}
        result, _ = self._call(lamp)
        self.assertAlmostEqual(result["run_length_m"], 0.55 + 0.30 + 0.05, places=6)

    def test_run_length_present_on_the_prebaked_path_too(self) -> None:
        result, _ = asyncio.run(handle_place_listing(
            self.store,
            listings=self.memory,
            args=self._good(),
            current_frame_id=_FRAME,
            prebaked={"oak side table": _ARTIFACT},
        ))
        self.assertAlmostEqual(result["run_length_m"], 0.55, places=6)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_listings.HandlePlaceListingTests -v`
Expected: FAIL — `ImportError: cannot import name 'handle_place_listing'`.

- [ ] **Step 3: Write the handler**

Append to `provider/coordinator/listings.py`. Add these imports at the top of the file, below the existing `from typing import Any`:

```python
from coordinator.jobs import JobStore, session_generation_busy
from coordinator.layout import pack_offsets, run_length
from coordinator.prebaked import lookup
from protocol.ids import new_ulid

MIN_AXIS_M = 0.05
MAX_AXIS_M = 3.0
MAX_NAME_CHARS = 40
_FRAME_TARGETS = frozenset({"capture_hint", "pointing", "image_point", "image_box"})
```

Then append:

```python
def _valid_extents(value: Any) -> list[float] | None:
    """Three numbers, each inside the axis bounds. Anything else is None."""
    if not isinstance(value, list) or len(value) != 3:
        return None
    axes: list[float] = []
    for axis in value:
        if isinstance(axis, bool) or not isinstance(axis, (int, float)):
            return None
        number = float(axis)
        if number < MIN_AXIS_M or number > MAX_AXIS_M:
            return None
        axes.append(number)
    return axes


def _valid_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > MAX_NAME_CHARS:
        return None
    return name


def _valid_target(value: Any, current_frame_id: str | None) -> dict | None:
    """Image-space target on the frame we just captured. Never a world point."""
    if not isinstance(value, dict):
        return None
    if value.get("type") not in _FRAME_TARGETS:
        return None
    if not current_frame_id or value.get("frame_id") != current_frame_id:
        return None
    return dict(value)


async def handle_place_listing(
    store: JobStore,
    *,
    listings: "ListingMemory",
    args: dict[str, Any],
    current_frame_id: str | None,
    prebaked: dict[str, str],
    queue_fn: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate one place_listing call. Returns (tool_result, coordinator_op).

    The caller owns sending the op: this runs inside a tool round with no turn
    id or stage epoch available.
    """
    name = _valid_name(args.get("name"))
    extents = _valid_extents(args.get("extent_m"))
    target = _valid_target(args.get("target"), current_frame_id)
    if name is None or extents is None or target is None:
        return {"error": "invalid"}, None

    listings.record(name, extents, current_frame_id, "page")
    offset_m = _offset_for(listings, name)

    artifact_id = lookup(prebaked, name)
    row_extents = [row["extent_m"] for row in listings.rows()]
    if artifact_id is not None:
        job_id = artifact_id
        store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": current_frame_id,
            "target": target,
            "object_id": None,
            "status": "ready",
            "extent_m": extents,
            "planted": True,
        }
        return (
            {
                "listed": name,
                "extent_m": extents,
                "job_id": job_id,
                "run_length_m": run_length(row_extents),
            },
            _place_op(job_id, extents, target, offset_m),
        )

    from workers.gen_client import BusyError

    if session_generation_busy(store):
        return {"error": "busy"}, None

    job_id = new_ulid()
    store.jobs[job_id] = {
        "job_id": job_id,
        "frame_id": current_frame_id,
        "target": target,
        "object_id": None,
        "status": "queued",
        "extent_m": extents,
        "planted": True,
    }
    if queue_fn is not None:
        try:
            await _maybe_await(queue_fn(
                job_id=job_id,
                frame_id=current_frame_id,
                jpeg_b64=None,
                prompt=name,
                mask_png_b64=None,
                extent_m=extents,
            ))
        except BusyError:
            store.jobs.pop(job_id, None)
            return {"error": "busy"}, None
    return (
        {
            "listed": name,
            "extent_m": extents,
            "job_id": job_id,
            "run_length_m": run_length(row_extents),
        },
        _place_op(job_id, extents, target, offset_m),
    )


def _offset_for(listings: "ListingMemory", name: str) -> float:
    """Frame-relative centre offset for this row, in recorded order (spec §7.1).

    Rows keep their insertion position when a repeat placement updates them, so
    an item does not jump sideways when the wearer restates its size.
    """
    rows = listings.rows()
    offsets = pack_offsets([row["extent_m"] for row in rows])
    if not offsets:
        return 0.0
    key = normalize_name(name)
    for index, row in enumerate(rows):
        if normalize_name(row["name"]) == key:
            return offsets[index]
    return 0.0


def _place_op(job_id: str, extents: list[float], target: dict, offset_m: float) -> dict[str, Any]:
    """Partial place_generated. _run_turn adds op_id, turn_id, stage_epoch."""
    return {
        "kind": "place_generated",
        "job_id": job_id,
        "extent_m": list(extents),
        "offset_m": float(offset_m),
        "target": dict(target),
        "drawing_id": None,
    }


async def _maybe_await(value: Any) -> Any:
    import inspect as inspect_module

    if inspect_module.iscoroutine(value):
        return await value
    return value
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_listings -v`
Expected: PASS, 27 tests.

- [ ] **Step 5: Make the busy check shared, not duplicated**

`coordinator/jobs.py` already has the one-worker-at-a-time rule as
`_session_generation_busy`. Reuse it rather than copying the loop into
`listings.py`. In `provider/coordinator/jobs.py`, rename it and update its one
caller:

```python
def session_generation_busy(store: JobStore) -> bool:
    """True while any worker job is queued or running (one job at a time)."""
    for job in store.jobs.values():
        if job.get("status") in ("queued", "running"):
            return True
    return False
```

and inside `handle_start_generation`, change `if _session_generation_busy(store):`
to `if session_generation_busy(store):`.

Confirm nothing else referenced the old private name:

Run: `cd provider && grep -rn "_session_generation_busy" --include=*.py .`
Expected: no matches. If a test references it, update that test to the new name.

- [ ] **Step 6: Add listings to session state****

In `provider/coordinator/session.py`, add the import beside the existing job import:

```python
from coordinator.listings import ListingMemory
```

and in `CoordinatorState.__init__`, immediately after `self.jobs = JobStore()`:

```python
        self.listings = ListingMemory()
        self.prebaked: dict[str, str] = {}
```

and in `clear_voice`, after `self.pending_ops.clear()`:

```python
        self.listings.clear()
```

- [ ] **Step 6: Write the failing planner test**

Create `provider/tests/test_place_listing_turn.py`:

```python
from __future__ import annotations

import asyncio
import unittest

from coordinator.jobs import JobStore
from coordinator.listings import ListingMemory
from coordinator.planner import MAX_OPS_PER_TURN, YibuPlanner

_FRAME = "01k5j8g0008q3m7b2d6h9n4r5v"
_TARGET = {"type": "capture_hint", "frame_id": _FRAME}
_ARTIFACT = "01m2xbae3n81b4scq0k83teqjw"


class CoordinatorOpPlumbingTests(unittest.TestCase):
    def _planner(self) -> YibuPlanner:
        planner = YibuPlanner(complete_fn=None, execute_fn=None)
        planner.bind_tools(
            jobs=JobStore(),
            jpeg_b64="",
            frame_id=_FRAME,
            listings=ListingMemory(),
            prebaked={"oak side table": _ARTIFACT},
        )
        return planner

    def test_place_listing_returns_result_and_records_op(self) -> None:
        planner = self._planner()
        result = asyncio.run(planner._dispatch_tool("place_listing", {
            "name": "oak side table",
            "extent_m": [0.55, 0.40, 0.72],
            "target": _TARGET,
        }))
        self.assertNotIn("error", result)
        self.assertEqual(len(planner._coordinator_ops), 1)
        self.assertEqual(planner._coordinator_ops[0]["kind"], "place_generated")

    def test_refused_place_listing_records_no_op(self) -> None:
        planner = self._planner()
        result = asyncio.run(planner._dispatch_tool("place_listing", {
            "name": "oak side table", "target": _TARGET,
        }))
        self.assertEqual(result["error"], "invalid")
        self.assertEqual(planner._coordinator_ops, [])

    def test_bind_tools_resets_pending_ops(self) -> None:
        planner = self._planner()
        planner._coordinator_ops.append({"kind": "place_generated"})
        planner.bind_tools(jobs=JobStore(), jpeg_b64="", frame_id=_FRAME)
        self.assertEqual(planner._coordinator_ops, [])

    def test_coordinator_ops_lead_and_respect_the_cap(self) -> None:
        planner = self._planner()
        for index in range(4):
            planner._coordinator_ops.append({"kind": "place_generated", "index": index})
        merged = planner._merge_ops([], _FRAME)
        self.assertEqual(len(merged), MAX_OPS_PER_TURN)
        self.assertEqual([op["index"] for op in merged], [0, 1, 2])
```

- [ ] **Step 7: Run it to verify it fails**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_place_listing_turn -v`
Expected: FAIL — `TypeError: bind_tools() got an unexpected keyword argument 'listings'`.

- [ ] **Step 8: Wire the planner**

In `provider/coordinator/planner.py`, in `YibuPlanner.__init__`, beside the other bound-tool fields:

```python
        self._listings: Any | None = None
        self._prebaked: dict[str, str] = {}
        self._coordinator_ops: list[dict] = []
```

Change `bind_tools` to accept and reset them:

```python
    def bind_tools(
        self,
        *,
        jobs: Any,
        jpeg_b64: str | None,
        frame_id: str | None,
        inspect_fn: Any | None = None,
        queue_fn: Any | None = None,
        listings: Any | None = None,
        prebaked: dict[str, str] | None = None,
    ) -> None:
        """Attach session tool context (called by the turn loop per turn)."""
        self._jobs = jobs
        self._jpeg_b64 = jpeg_b64
        self._frame_id = frame_id
        self._inspect_fn = inspect_fn
        self._queue_fn = queue_fn
        self._listings = listings
        self._prebaked = prebaked or {}
        self._coordinator_ops = []
```

In `_dispatch_tool`, add this branch after the `start_generation` branch:

```python
        if name == "place_listing":
            from coordinator.listings import handle_place_listing

            result, op = await handle_place_listing(
                self._jobs,
                listings=self._listings,
                args=dict(arguments),
                current_frame_id=self._frame_id,
                prebaked=self._prebaked,
                queue_fn=self._queue_fn,
            )
            if op is not None:
                self._coordinator_ops.append(op)
            return result
```

Add the merge helper to `YibuPlanner`:

```python
    def _merge_ops(self, model_ops: list[dict], frame_id: str | None) -> list[dict]:
        """Coordinator-authored ops lead, model ops follow, cap applies to both.

        Coordinator ops are already frame-anchored and are validated by
        _to_scene_op in the turn loop, which is the single send-time gate.
        """
        merged = list(self._coordinator_ops) + list(model_ops)
        return merged[:MAX_OPS_PER_TURN]
```

In `plan()`, replace the two `accept_model_ops(...)` call sites so both merge. The tool path line becomes:

```python
        ops = self._merge_ops(
            accept_model_ops(collected, frame_id) if frame_id else [], frame_id
        )
```

and the legacy path line becomes:

```python
            ops = self._merge_ops(
                accept_model_ops(raw_ops, frame_id) if frame_id else [], frame_id
            )
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_place_listing_turn tests.test_listings -v`
Expected: PASS, 31 tests.

Then run the whole suite to catch regressions from the `bind_tools` signature change:

Run: `cd provider && . .venv/bin/activate && python -m unittest discover -s tests -v`
Expected: PASS. If a test double calls `bind_tools` positionally, fix the call to use keywords.

- [ ] **Step 10: Commit**

```bash
git add provider/coordinator/listings.py provider/coordinator/planner.py provider/coordinator/session.py provider/tests/test_listings.py provider/tests/test_place_listing_turn.py
git commit -m "Coordinator: place_listing handler, listing memory, coordinator ops"
```

---

## Task 7: Plant at accept, and only once

**Files:**
- Modify: `provider/coordinator/turn.py`
- Test: `provider/tests/test_turn_tools.py`

**Interfaces:**
- Consumes: `extent_m` on the job record and `planted` (Task 6).
- Produces: `build_place_generated(*, job_id, turn_id, stage_epoch, target, extent_m=None)`. `on_job_terminal` no longer emits for a planted job.

**Why:** a job with extents is planted when accepted, so `on_job_terminal` emitting again would place the same object twice.

- [ ] **Step 1: Write the failing tests**

Append to `provider/tests/test_turn_tools.py`, inside `class PlaceGeneratedTests`:

```python
    def test_extent_m_is_carried_onto_the_op(self) -> None:
        op = build_place_generated(
            job_id=new_ulid(), turn_id=2, stage_epoch=1,
            target=self.target, extent_m=[0.55, 0.40, 0.72],
        )
        validate_instance("scene_op", op)
        self.assertEqual(op["extent_m"], [0.55, 0.40, 0.72])

    def test_op_without_extents_still_validates(self) -> None:
        op = build_place_generated(
            job_id=new_ulid(), turn_id=2, stage_epoch=1, target=self.target,
        )
        validate_instance("scene_op", op)
        self.assertNotIn("extent_m", op)

    def test_planted_job_does_not_emit_a_second_op(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "ready",
            "stage_epoch": 1,
            "extent_m": [0.55, 0.40, 0.72],
            "planted": True,
        }

        async def send(op):
            self.sent.append(op)
            return {"status": "placed", "op_id": op["op_id"]}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(self.sent, [], "already planted; no second op")
        self.assertEqual(self.announced, [{"status": "ready", "planted": True}])

    def test_unplanted_ready_job_still_emits(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "queued",
            "stage_epoch": 1,
        }
        mark_ready(self.store, job_id)

        async def send(op):
            self.sent.append(op)
            return {"status": "placed", "op_id": op["op_id"]}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("extent_m", self.sent[0])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_turn_tools.PlaceGeneratedTests -v`
Expected: FAIL — `TypeError: build_place_generated() got an unexpected keyword argument 'extent_m'`.

- [ ] **Step 3: Implement**

In `provider/coordinator/turn.py`, replace `build_place_generated` with:

```python
def build_place_generated(
    *,
    job_id: str,
    turn_id: int,
    stage_epoch: int,
    target: dict,
    extent_m: list[float] | None = None,
) -> dict:
    op = {
        "op_id": new_ulid(),
        "turn_id": turn_id,
        "stage_epoch": stage_epoch,
        "kind": "place_generated",
        "drawing_id": None,
        "job_id": job_id,
        "target": target,
    }
    if extent_m is not None:
        op["extent_m"] = [float(axis) for axis in extent_m]
    validate_instance("scene_op", op)
    return op
```

In `on_job_terminal`, immediately after the `if status not in ("ready", "failed"):` guard and before the stage-epoch check, insert:

```python
    if job.get("extent_m") and job.get("planted"):
        # Planted when the listing was accepted, so a stated-size box has been
        # in the room since before the mesh existed. Emitting again would
        # place the same object twice. Quest is already fetching the artifact.
        await complete_final_fn({"status": "ready", "planted": True})
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd provider && . .venv/bin/activate && python -m unittest tests.test_turn_tools -v`
Expected: PASS, including the four pre-existing `PlaceGeneratedTests`.

- [ ] **Step 5: Commit**

```bash
git add provider/coordinator/turn.py provider/tests/test_turn_tools.py
git commit -m "Coordinator: plant sized ops at accept, never twice"
```

---

## Task 8: Unity parses `extent_m`

**Files:**
- Modify: `QuestDemo/Assets/Spatial/ProtocolJson.cs`
- Create: `QuestDemo/Assets/Tests/Editor/ProtocolExtentTests.cs`

**Interfaces:**
- Consumes: the wire fields from Task 1.
- Produces: `SceneOpMsg.HasExtentM` (`bool`), `SceneOpMsg.ExtentM` (`Vector3`, `x` = width, `y` = depth, `z` = height as received in listing order), `SceneOpMsg.HasOffsetM` (`bool`), and `SceneOpMsg.OffsetM` (`float`, metres along the width axis). Consumed by Tasks 9 and 10.

Note the deliberately literal mapping: `ExtentM.x/y/z` hold the wire array's first/second/third entries. Task 9 decides which becomes Unity's width, height, and depth.

- [ ] **Step 1: Write the failing test**

Create `QuestDemo/Assets/Tests/Editor/ProtocolExtentTests.cs`:

```csharp
using NUnit.Framework;

public class ProtocolExtentTests
{
    const string WithExtent =
        "{\"op_id\":\"01k5j8g0019q3m7b2d6h9n4r5v\",\"turn_id\":4,\"stage_epoch\":1," +
        "\"kind\":\"place_generated\",\"drawing_id\":null," +
        "\"job_id\":\"01m2xbae3n81b4scq0k83teqjw\"," +
        "\"extent_m\":[0.55,0.40,0.72]," +
        "\"target\":{\"type\":\"capture_hint\",\"frame_id\":\"01k5j8g0008q3m7b2d6h9n4r5v\"}}";

    const string WithoutExtent =
        "{\"op_id\":\"01k5j8g0019q3m7b2d6h9n4r5v\",\"turn_id\":4,\"stage_epoch\":1," +
        "\"kind\":\"place_generated\",\"drawing_id\":null," +
        "\"job_id\":\"01m2xbae3n81b4scq0k83teqjw\"," +
        "\"target\":{\"type\":\"capture_hint\",\"frame_id\":\"01k5j8g0008q3m7b2d6h9n4r5v\"}}";

    [Test]
    public void ParsesExtentMInWireOrder()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithExtent, out op));
        Assert.IsTrue(op.HasExtentM);
        Assert.AreEqual(0.55f, op.ExtentM.x, 1e-5f);
        Assert.AreEqual(0.40f, op.ExtentM.y, 1e-5f);
        Assert.AreEqual(0.72f, op.ExtentM.z, 1e-5f);
    }

    [Test]
    public void AbsentExtentLeavesHasExtentMFalse()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithoutExtent, out op));
        Assert.IsFalse(op.HasExtentM);
    }

    [Test]
    public void ParsesOffsetM()
    {
        string withOffset = WithExtent.Replace(
            "\"extent_m\":[0.55,0.40,0.72]", "\"extent_m\":[0.55,0.40,0.72],\"offset_m\":1.15");
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(withOffset, out op));
        Assert.IsTrue(op.HasOffsetM);
        Assert.AreEqual(1.15f, op.OffsetM, 1e-5f);
    }

    [Test]
    public void AbsentOffsetLeavesHasOffsetMFalse()
    {
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(WithExtent, out op));
        Assert.IsFalse(op.HasOffsetM);
        Assert.AreEqual(0f, op.OffsetM, 1e-5f);
    }

    [Test]
    public void ShortArrayIsIgnoredNotPartiallyApplied()
    {
        string twoAxes = WithExtent.Replace("[0.55,0.40,0.72]", "[0.55,0.40]");
        ProtocolJson.SceneOpMsg op;
        Assert.IsTrue(ProtocolJson.TryParseSceneOp(twoAxes, out op));
        Assert.IsFalse(op.HasExtentM, "a malformed array must not claim an extent");
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter ProtocolExtentTests -testResults /tmp/extent-red.xml -logFile /tmp/unity-extent-red.log'`
Expected: FAIL to compile — `SceneOpMsg` has no `HasExtentM`, and no `ExtentM`.

- [ ] **Step 3: Add the fields**

In `QuestDemo/Assets/Spatial/ProtocolJson.cs`, in `class SceneOpMsg`, after `public string JobId;`:

```csharp
        public bool HasExtentM;
        public Vector3 ExtentM;
        public bool HasOffsetM;
        public float OffsetM;
```

Confirm the file's usings include `UnityEngine`; if `Vector3` does not resolve, add `using UnityEngine;` at the top.

- [ ] **Step 4: Parse it**

`TryGetDouble` in this file is **key-based** (`IndexOfKey`), so it cannot read
bare array elements. Add an index-based helper next to it:

```csharp
    /// <summary>Read one element of a bracketed numeric array by position.</summary>
    static bool TryGetIndexedDouble(string arrayJson, int index, out double value)
    {
        value = 0;
        if (string.IsNullOrEmpty(arrayJson) || index < 0)
            return false;
        string body = arrayJson.Trim();
        if (body.StartsWith("["))
            body = body.Substring(1);
        if (body.EndsWith("]"))
            body = body.Substring(0, body.Length - 1);
        string[] parts = body.Split(',');
        if (index >= parts.Length)
            return false;
        return double.TryParse(parts[index].Trim(),
            NumberStyles.Float, CultureInfo.InvariantCulture, out value);
    }
```

Then, in `TryParseSceneOp`, immediately after the `job_id` parse block:

```csharp
        int extentAt = IndexOfKey(payloadJson, "extent_m", 0);
        if (extentAt >= 0)
        {
            int bracketAt = payloadJson.IndexOf('[', extentAt);
            string arr;
            int endAt;
            if (bracketAt >= 0 && ExtractBracketed(payloadJson, bracketAt, out arr, out endAt))
            {
                double x, y, z;
                if (TryGetIndexedDouble(arr, 0, out x) &&
                    TryGetIndexedDouble(arr, 1, out y) &&
                    TryGetIndexedDouble(arr, 2, out z))
                {
                    parsed.HasExtentM = true;
                    parsed.ExtentM = new Vector3((float)x, (float)y, (float)z);
                }
            }
        }
        double offset;
        if (TryGetDouble(payloadJson, "offset_m", out offset))
        {
            parsed.HasOffsetM = true;
            parsed.OffsetM = (float)offset;
        }
```

All three axes must parse before `HasExtentM` is set: a partially read extent
is not a size claim, and the short-array test in Step 1 pins that.

- [ ] **Step 5: Run the test to verify it passes**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter ProtocolExtentTests -testResults /tmp/extent-green.xml -logFile /tmp/unity-extent-green.log'`
Expected: PASS, 5/5. Confirm `result="Passed" total="5"` in `/tmp/extent-green.xml`.

- [ ] **Step 6: Commit**

```bash
git add QuestDemo/Assets/Spatial/ProtocolJson.cs QuestDemo/Assets/Tests/Editor/ProtocolExtentTests.cs
git commit -m "Quest: parse extent_m from place_generated"
```

---

## Task 9: The listed box and the uniform fit

**Files:**
- Create: `QuestDemo/Assets/Spatial/ListingBox.cs`
- Create: `QuestDemo/Assets/Tests/Editor/ListingBoxTests.cs`

**Interfaces:**
- Consumes: `HasExtentM` / `ExtentM` (Task 8).
- Produces: `ListingBox.FitScale(Vector3 meshSize, Vector3 boxSize) -> float` (one uniform factor), `ListingBox.IsApproximate(Vector3 meshSize, Vector3 boxSize) -> bool` (25% rule), `ListingBox.ToBoxSize(Vector3 extentM) -> Vector3` (wire order → Unity `w, h, d`), and `ListingBox.Create(Vector3 boxSize, Vector3 surfacePoint, Vector3 facing, string drawingId) -> GameObject`. Consumed by Task 10.

**Axis mapping:** the wire order is `w, d, h` (front width, front-to-back depth, vertical height). Unity wants `(x, y, z) = (width, height, depth)`, so `ToBoxSize` maps `extentM.x → x`, `extentM.z → y`, `extentM.y → z`.

- [ ] **Step 1: Write the failing test**

Create `QuestDemo/Assets/Tests/Editor/ListingBoxTests.cs`:

```csharp
using NUnit.Framework;
using UnityEngine;

public class ListingBoxTests
{
    [Test]
    public void WireOrderBecomesUnityWidthHeightDepth()
    {
        // wire w, d, h = 0.55, 0.40, 0.72  ->  Unity (x, y, z) = (w, h, d)
        Vector3 size = ListingBox.ToBoxSize(new Vector3(0.55f, 0.40f, 0.72f));
        Assert.AreEqual(0.55f, size.x, 1e-5f);
        Assert.AreEqual(0.72f, size.y, 1e-5f);
        Assert.AreEqual(0.40f, size.z, 1e-5f);
    }

    [Test]
    public void FitShrinksTheOversizedAxisOnly()
    {
        // box (w,h,d) = 0.55, 0.72, 0.40 ; mesh twice as wide as the box
        Vector3 box = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 mesh = new Vector3(1.10f, 0.72f, 0.40f);
        float scale = ListingBox.FitScale(mesh, box);
        Assert.AreEqual(0.5f, scale, 1e-4f);
    }

    [Test]
    public void FitIsUniformSoProportionsSurvive()
    {
        Vector3 box = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 mesh = new Vector3(1.10f, 1.80f, 0.80f);
        float scale = ListingBox.FitScale(mesh, box);
        Vector3 scaled = mesh * scale;
        // the mesh's own ratio is preserved: it is the same mesh, one factor
        Assert.AreEqual(mesh.x / mesh.y, scaled.x / scaled.y, 1e-4f);
        Assert.LessOrEqual(scaled.x, box.x + 1e-4f);
        Assert.LessOrEqual(scaled.y, box.y + 1e-4f);
        Assert.LessOrEqual(scaled.z, box.z + 1e-4f);
    }

    [Test]
    public void NeverEnlargesAMeshThatAlreadyFits()
    {
        Vector3 box = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 mesh = new Vector3(0.10f, 0.10f, 0.10f);
        Assert.AreEqual(1f, ListingBox.FitScale(mesh, box), 1e-4f);
    }

    [Test]
    public void DegenerateSizesDoNotDivideByZero()
    {
        Assert.AreEqual(1f, ListingBox.FitScale(Vector3.zero, new Vector3(1f, 1f, 1f)), 1e-4f);
        Assert.AreEqual(1f, ListingBox.FitScale(new Vector3(1f, 1f, 1f), Vector3.zero), 1e-4f);
    }

    [Test]
    public void AspectThresholdTripsAtTwentyFivePercent()
    {
        Vector3 box = new Vector3(0.50f, 0.50f, 0.50f);
        Assert.IsFalse(ListingBox.IsApproximate(new Vector3(0.60f, 0.50f, 0.50f), box));
        Assert.IsTrue(ListingBox.IsApproximate(new Vector3(0.64f, 0.50f, 0.50f), box));
    }

    [Test]
    public void AspectThresholdUsesTheWorstAxis()
    {
        Vector3 box = new Vector3(0.50f, 0.50f, 0.50f);
        Vector3 skewed = new Vector3(0.51f, 0.51f, 0.10f);
        Assert.IsTrue(ListingBox.IsApproximate(skewed, box));
    }

    [Test]
    public void BoxIsCreatedWithTheRequestedSize()
    {
        GameObject box = ListingBox.Create(
            new Vector3(0.55f, 0.72f, 0.40f), Vector3.zero, Vector3.forward, "drawing-1");
        Assert.IsNotNull(box);
        Assert.AreEqual(0.55f, box.transform.localScale.x, 1e-4f);
        Assert.AreEqual(0.72f, box.transform.localScale.y, 1e-4f);
        Assert.AreEqual(0.40f, box.transform.localScale.z, 1e-4f);
        Object.DestroyImmediate(box);
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter ListingBoxTests -testResults /tmp/box-red.xml -logFile /tmp/unity-box-red.xml.log'`
Expected: FAIL to compile — `ListingBox` does not exist.

- [ ] **Step 3: Write the implementation**

Create `QuestDemo/Assets/Spatial/ListingBox.cs`:

```csharp
using UnityEngine;

/// <summary>
/// The listed-size claim for one placed product (spec §6). The box is the
/// size the page stated; a generated mesh is fitted inside it, never the
/// other way round. All fit math is static and pure so it is EditMode
/// testable without a headset.
/// </summary>
public static class ListingBox
{
    /// <summary>Mesh may differ from the listed box by this much per axis.</summary>
    public const float AspectTolerance = 0.25f;

    /// <summary>Wire extent order is (w, d, h); Unity wants (x, y, z) = (w, h, d).</summary>
    public static Vector3 ToBoxSize(Vector3 extentM)
    {
        return new Vector3(extentM.x, extentM.z, extentM.y);
    }

    /// <summary>
    /// One uniform factor that fits <paramref name="meshSize"/> inside
    /// <paramref name="boxSize"/>. Never enlarges; never stretches an axis,
    /// because a per-axis fit would make a wrong mesh look right.
    /// </summary>
    public static float FitScale(Vector3 meshSize, Vector3 boxSize)
    {
        if (meshSize.x <= 0f || meshSize.y <= 0f || meshSize.z <= 0f)
            return 1f;
        if (boxSize.x <= 0f || boxSize.y <= 0f || boxSize.z <= 0f)
            return 1f;
        float factor = Mathf.Min(
            boxSize.x / meshSize.x,
            Mathf.Min(boxSize.y / meshSize.y, boxSize.z / meshSize.z));
        return factor > 0f && factor < 1f ? factor : 1f;
    }

    /// <summary>True when the mesh's proportions miss the listing by too much.</summary>
    public static bool IsApproximate(Vector3 meshSize, Vector3 boxSize)
    {
        if (meshSize.x <= 0f || meshSize.y <= 0f || meshSize.z <= 0f)
            return false;
        if (boxSize.x <= 0f || boxSize.y <= 0f || boxSize.z <= 0f)
            return false;
        return WorstAxisDrift(meshSize, boxSize) > AspectTolerance;
    }

    static float WorstAxisDrift(Vector3 meshSize, Vector3 boxSize)
    {
        float worst = 0f;
        float[] mesh = { meshSize.x, meshSize.y, meshSize.z };
        float[] box = { boxSize.x, boxSize.y, boxSize.z };
        for (int i = 0; i < 3; i++)
        {
            float ratio = mesh[i] / box[i];
            float drift = Mathf.Abs(ratio - 1f);
            if (drift > worst)
                worst = drift;
        }
        return worst;
    }

    /// <summary>
    /// A translucent box at the stated size, resting on the surface: its
    /// centre is half its height above <paramref name="surfacePoint"/> so the
    /// base sits on the floor rather than the middle sinking into it.
    /// </summary>
    public static GameObject Create(
        Vector3 boxSize, Vector3 surfacePoint, Vector3 facing, string drawingId)
    {
        GameObject root = GameObject.CreatePrimitive(PrimitiveType.Cube);
        root.name = "Listing_" + drawingId;
        Collider collider = root.GetComponent<Collider>();
        if (collider != null)
            Object.Destroy(collider);
        root.transform.localScale = boxSize;
        Vector3 fwd = new Vector3(facing.x, 0f, facing.z);
        if (fwd.sqrMagnitude < 1e-6f)
            fwd = Vector3.forward;
        root.transform.rotation = Quaternion.LookRotation(fwd.normalized, Vector3.up);
        root.transform.position = surfacePoint + Vector3.up * (boxSize.y / 2f);
        Renderer rend = root.GetComponent<Renderer>();
        if (rend != null)
        {
            rend.material = new Material(Shader.Find("Unlit/Color"));
            Color c = PulsingRing.RingColor;
            c.a = 0.25f;
            rend.material.color = c;
        }
        return root;
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter ListingBoxTests -testResults /tmp/box-green.xml -logFile /tmp/unity-box-green.log'`
Expected: PASS, 8/8. Confirm `result="Passed" total="8"` in `/tmp/box-green.xml`.

- [ ] **Step 5: Commit**

```bash
git add QuestDemo/Assets/Spatial/ListingBox.cs QuestDemo/Assets/Tests/Editor/ListingBoxTests.cs
git commit -m "Quest: listed box with uniform fit math"
```

---

## Task 10: GeneratedMeshPlacer fits the box and registers the drawing

**Files:**
- Modify: `QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs`
- Modify: `QuestDemo/Assets/Spatial/DrawingStore.cs`
- Modify: `QuestDemo/Assets/Spatial/CoordinatorClient.cs`
- Modify: `QuestDemo/Assets/Tests/Editor/GeneratedMeshPlacerTests.cs`

**Interfaces:**
- Consumes: `ListingBox.ToBoxSize`, `ListingBox.FitScale`, `ListingBox.IsApproximate`, `ListingBox.Create` (Task 9); `HasExtentM` / `ExtentM` (Task 8).
- Produces: `DrawingStore.PlaceGenerated(Vector3 point, Vector3 normal, string drawingId, Vector3? extentM, float offsetM = 0f) -> GameObject`, `DrawingStore.MarkMeshFitted(string drawingId, bool approximate) -> void`. Consumed by Task 11.

**Why the store matters:** today generated roots bypass `DrawingStore` entirely, so the clutter cap, `Clear`, and revise cannot see them. Registering them is what makes the object addressable.

- [ ] **Step 1: Write the failing tests**

Replace the contents of `QuestDemo/Assets/Tests/Editor/GeneratedMeshPlacerTests.cs` with:

```csharp
using NUnit.Framework;
using UnityEngine;

public class GeneratedMeshPlacerTests
{
    [Test]
    public void ArtifactUrl_UsesJobIdOnly()
    {
        string url = GeneratedMeshPlacer.ArtifactUrl("10.0.0.8", 8766, "01k00000000000000000000001");
        Assert.AreEqual("http://10.0.0.8:8766/artifacts/01k00000000000000000000001.glb", url);
    }

    [Test]
    public void ArtifactUrl_RejectsDotDot()
    {
        Assert.IsNull(GeneratedMeshPlacer.ArtifactUrl("10.0.0.8", 8766, "../x"));
    }

    [Test]
    public void FetchAttemptBudgetIsBounded()
    {
        Assert.AreEqual(6, GeneratedMeshPlacer.MaxFetchAttempts);
        Assert.Greater(GeneratedMeshPlacer.FetchRetrySeconds, 0f);
    }

    [Test]
    public void FailureLeavesTheListedBoxNotASmallCube()
    {
        // A 55 x 72 x 40 cm listing must never degrade to a 10 cm cube. This
        // is the real failure path: no mesh ever arrives, so the store's
        // listed box is what remains.
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "d-fail", new Vector3(0.55f, 0.40f, 0.72f));
        Transform box = placed.transform.GetChild(0);
        Assert.AreEqual(0.55f, box.localScale.x, 1e-4f);
        Assert.AreEqual(0.72f, box.localScale.y, 1e-4f);
        Assert.AreEqual(0.40f, box.localScale.z, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void StoreRegistersAGeneratedDrawing()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "drawing-1", new Vector3(0.55f, 0.40f, 0.72f));
        Assert.IsNotNull(placed);
        Assert.AreEqual(1, store.Count);
        Assert.IsTrue(store.HasGenerated("drawing-1"));
        Object.DestroyImmediate(go);
    }

    [Test]
    public void GeneratedDrawingCountsTowardTheClutterCap()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        for (int i = 0; i < DrawingStore.MaxDrawings; i++)
            store.PlaceGenerated(Vector3.zero, Vector3.up, "drawing-" + i, null);
        Assert.AreEqual(DrawingStore.MaxDrawings, store.Count);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void ZeroOffsetLeavesTheBoxOnTheHit()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            new Vector3(2f, 0f, 3f), Vector3.up, "d0", new Vector3(0.55f, 0.40f, 0.72f), 0f);
        Assert.AreEqual(2f, placed.transform.position.x, 1e-4f);
        Assert.AreEqual(3f, placed.transform.position.z, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void NonZeroOffsetShiftsAlongTheWidthAxisOnly()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "d0", new Vector3(0.55f, 0.40f, 0.72f), 1.15f);
        // Facing flattens to +Z, so the width axis is +X. Height is the box's
        // half-height, untouched by the offset.
        Assert.AreEqual(1.15f, Mathf.Abs(placed.transform.position.x), 1e-4f);
        Assert.AreEqual(0f, placed.transform.position.z, 1e-4f);
        Assert.AreEqual(0.36f, placed.transform.position.y, 1e-4f);
        Object.DestroyImmediate(go);
    }

    [Test]
    public void RootAndBoxCoincideSoAGrabCannotDoubleOffset()
    {
        var go = new GameObject("store");
        var store = go.AddComponent<DrawingStore>();
        GameObject placed = store.PlaceGenerated(
            Vector3.zero, Vector3.up, "d0", new Vector3(0.55f, 0.40f, 0.72f));
        Transform box = placed.transform.GetChild(0);
        Assert.AreEqual(0f, box.localPosition.magnitude, 1e-5f);
        Assert.AreEqual(placed.transform.position.y, box.position.y, 1e-5f);
        Object.DestroyImmediate(go);
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter GeneratedMeshPlacerTests -testResults /tmp/gen-red.xml -logFile /tmp/unity-gen-red.log'`
Expected: FAIL to compile — no `MaxFetchAttempts`, no `PlaceGenerated`, no `HasGenerated`.

- [ ] **Step 3: Add the store surface**

In `QuestDemo/Assets/Spatial/DrawingStore.cs`, add this record beside `ProceduralRecord`:

```csharp
    /// <summary>Generated furniture record (spec §6.1): box, mesh, one id.</summary>
    sealed class GeneratedRecord
    {
        public GameObject Root;
        public GameObject Mesh;
        public Vector3 BoxSize;
        public bool Approximate;
    }

    readonly Dictionary<string, GeneratedRecord> _generated = new Dictionary<string, GeneratedRecord>();
```

and these members:

```csharp
    /// <summary>
    /// Register one generated placement as a normal drawing so remove, undo,
    /// the clutter cap, and revise all see it. Returns the root.
    /// </summary>
    /// <param name="offsetM">
    /// Frame-relative centre offset along the width axis (spec §7.1). The
    /// first item in a row is 0, so a lone placement lands on the hit.
    /// </param>
    public GameObject PlaceGenerated(
        Vector3 point, Vector3 normal, string drawingId, Vector3? extentM, float offsetM = 0f)
    {
        Prune();
        EvictIfNeeded();
        GameObject root = new GameObject("Drawing_" + drawingId);
        Vector3 n = normal.sqrMagnitude < 1e-6f ? Vector3.up : normal.normalized;
        Vector3 facing = -n;
        Vector3 boxSize = extentM.HasValue
            ? ListingBox.ToBoxSize(extentM.Value)
            : Vector3.one * 0.12f;
        Vector3 planted = point;
        if (Mathf.Abs(offsetM) > 1e-6f)
        {
            Vector3 flat = new Vector3(facing.x, 0f, facing.z);
            if (flat.sqrMagnitude < 1e-6f)
                flat = Vector3.forward;
            // Right-hand width axis of the placement frame: rows run along it.
            Vector3 widthAxis = Vector3.Cross(Vector3.up, flat.normalized);
            planted += widthAxis * offsetM;
        }
        GameObject box = ListingBox.Create(boxSize, planted, facing, drawingId);
        // Root and box coincide: the root carries the pose, the box is its
        // visual at local zero. Grab locks the root's Y, so the two must not
        // be offset from each other or a drag would double the height.
        root.transform.position = box.transform.position;
        root.transform.rotation = box.transform.rotation;
        box.transform.SetParent(root.transform, false);
        box.transform.localPosition = Vector3.zero;
        root.transform.SetParent(null, true);
        TryAddAnchor(root);
        _marks.Add(root);
        _generated[drawingId] = new GeneratedRecord
        {
            Root = root,
            Mesh = null,
            BoxSize = boxSize,
            Approximate = false,
        };
        return root;
    }

    /// <summary>True when this drawing id is a generated placement.</summary>
    public bool HasGenerated(string drawingId)
    {
        return !string.IsNullOrEmpty(drawingId) && _generated.ContainsKey(drawingId);
    }

    /// <summary>
    /// Attach the imported mesh under the listed box and fit it uniformly.
    /// The box stays as the size claim; only its opacity changes.
    /// </summary>
    public void MarkMeshFitted(string drawingId, bool approximate)
    {
        GeneratedRecord rec = null;
        if (string.IsNullOrEmpty(drawingId) || !_generated.TryGetValue(drawingId, out rec))
            return;
        rec.Approximate = approximate;
        if (rec.Root == null)
            return;
        Renderer box = rec.Root.GetComponentInChildren<Renderer>();
        if (box != null && box.material != null)
        {
            Color c = box.material.color;
            c.a = approximate ? 0.35f : 0.12f;
            box.material.color = c;
        }
    }

    /// <summary>Fit an imported mesh object inside the recorded box.</summary>
    public bool FitMeshIntoBox(string drawingId, GameObject mesh)
    {
        GeneratedRecord rec = null;
        if (mesh == null || string.IsNullOrEmpty(drawingId) ||
            !_generated.TryGetValue(drawingId, out rec) || rec.Root == null)
            return false;
        Renderer[] renderers = mesh.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0)
            return false;
        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++)
            bounds.Encapsulate(renderers[i].bounds);
        float scale = ListingBox.FitScale(bounds.size, rec.BoxSize);
        mesh.transform.localScale = mesh.transform.localScale * scale;
        mesh.transform.SetParent(rec.Root.transform, true);
        rec.Mesh = mesh;
        MarkMeshFitted(drawingId, ListingBox.IsApproximate(bounds.size, rec.BoxSize));
        return true;
    }
```

In `Clear()`, add `_generated.Clear();` after `_procedural.Clear();`.

- [ ] **Step 4: Remove the unit-sphere fit and the placeholder cube**

In `QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs`, add the constants:

```csharp
    public const int MaxFetchAttempts = 6;
    public const float FetchRetrySeconds = 15f;
```

Replace the whole `using (UnityWebRequest req = UnityWebRequest.Get(url))` block with a retry loop. Note the coordinator only serves an artifact once its job is `ready` (`coordinator/artifacts.py`), and a sized job is planted before that, so a 404 here is expected and must be retried:

```csharp
        byte[] data = null;
        for (int attempt = 0; attempt < MaxFetchAttempts && data == null; attempt++)
        {
            if (attempt > 0)
                yield return new WaitForSeconds(FetchRetrySeconds);
            using (UnityWebRequest req = UnityWebRequest.Get(url))
            {
                req.downloadHandler = new DownloadHandlerBuffer();
                yield return req.SendWebRequest();
#if UNITY_2020_2_OR_NEWER
                bool ok = req.result == UnityWebRequest.Result.Success;
#else
                bool ok = !req.isNetworkError && !req.isHttpError;
#endif
                if (!ok)
                    continue;
                byte[] candidate = req.downloadHandler.data;
                if (candidate == null || candidate.Length < 12 || candidate.Length > MaxGlbBytes)
                    continue;
                if (!StartsWithGltf(candidate))
                    continue;
                data = candidate;
            }
        }
```

Then replace the tail of `FetchAndPlace` — everything from `GameObject root = null;` through the `client.EnqueueAck(op, "placed", ...)` call — with:

```csharp
        string drawingId = client.NewDrawingId != null
            ? client.NewDrawingId()
            : SpatialRuntime.NewFrameId();
        Vector3? extent = op.HasExtentM ? (Vector3?)op.ExtentM : null;
        if (store == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        GameObject drawing = store.PlaceGenerated(
            result.Point, result.Normal, drawingId, extent,
            op.HasOffsetM ? op.OffsetM : 0f);
        if (drawing == null)
        {
            client.EnqueueAck(op, "rejected", null, "invalid", null);
            yield break;
        }
        // The box is placed and real whether or not the mesh ever arrives.
        // ACK now: placement_ack 'placed' needs drawing_id + pin only.
        client.EnqueueAck(op, "placed", drawingId, null, "surface");
        Debug.Log("GENERATED_BOX_PLACED op=" + op.OpId + " job=" + jobId + " drawing=" + drawingId);

        if (data == null)
        {
            Debug.LogWarning("GeneratedMeshPlacer: no artifact after "
                + MaxFetchAttempts + " attempts; box stays at listed size job=" + jobId);
            client.NotifyMeshMissing(drawingId);
            yield break;
        }

        GameObject mesh = ImportGlb(data, jobId);
        if (mesh == null)
        {
            client.NotifyMeshMissing(drawingId);
            yield break;
        }
        if (!store.FitMeshIntoBox(drawingId, mesh))
            Object.Destroy(mesh);
```

Add the import as two static helpers with the yield kept in `FetchAndPlace`, because a coroutine cannot yield from a static method. This mirrors the original code's approach without blocking the main thread:

```csharp
    sealed class PendingImport
    {
        public GameObject Holder;
        public System.Threading.Tasks.Task<bool> Task;
        public string TmpPath;
    }

    /// <summary>Start the import. Caller yields on PendingImport.Task.</summary>
    static PendingImport BeginImport(byte[] data, string jobId)
    {
        string tmpPath;
        try
        {
            tmpPath = System.IO.Path.Combine(Application.temporaryCachePath, jobId + ".glb");
            System.IO.File.WriteAllBytes(tmpPath, data);
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("GeneratedMeshPlacer: cache write failed (" + e.GetType().Name + ")");
            return null;
        }
        GameObject holder = new GameObject("Generated_" + jobId);
        GLTFast.GltfAsset asset = holder.AddComponent<GLTFast.GltfAsset>();
        return new PendingImport
        {
            Holder = holder,
            Task = asset.Load("file://" + tmpPath),
            TmpPath = tmpPath,
        };
    }

    /// <summary>Resolve a finished import. Null when it did not produce a mesh.</summary>
    static GameObject FinishImport(PendingImport pending, string jobId)
    {
        if (pending == null)
            return null;
        try { System.IO.File.Delete(pending.TmpPath); } catch (System.Exception) { }
        bool ok = pending.Task.Status == System.Threading.Tasks.TaskStatus.RanToCompletion
            && pending.Task.Result
            && pending.Holder != null
            && pending.Holder.GetComponentsInChildren<Renderer>().Length > 0;
        if (ok)
            return pending.Holder;
        Debug.LogWarning("GeneratedMeshPlacer: import failed job=" + jobId);
        if (pending.Holder != null)
            Object.Destroy(pending.Holder);
        return null;
    }
```

and in `FetchAndPlace`:

```csharp
        PendingImport pending = BeginImport(data, jobId);
        if (pending == null)
        {
            client.NotifyMeshMissing(drawingId);
            yield break;
        }
        while (!pending.Task.IsCompleted)
            yield return null;
        GameObject mesh = FinishImport(pending, jobId);
        if (mesh == null)
        {
            client.NotifyMeshMissing(drawingId);
            yield break;
        }
        if (!store.FitMeshIntoBox(drawingId, mesh))
            Object.Destroy(mesh);
```

Delete `FitInsideUnitSphere` entirely, and delete the `GameObject.CreatePrimitive(PrimitiveType.Cube)` placeholder path. Do **not** add a failure-visual helper: the listed box that `store.PlaceGenerated` already created is the failure visual, and it stays at full stated size because nothing ever replaces it.

Update `TryHandle` to take the store and thread it through:

```csharp
    public static bool TryHandle(
        CoordinatorClient client,
        MonoBehaviour host,
        ProtocolJson.SceneOpMsg op,
        string laptopIpv4,
        int artifactPort,
        CaptureGeometryCache cache,
        DrawingStore store)
    {
        if (op == null || op.Kind != "place_generated")
            return false;
        if (host == null || client == null)
            return false;
        host.StartCoroutine(FetchAndPlace(client, op, laptopIpv4, artifactPort, cache, store));
        return true;
    }
```

and add `DrawingStore store` as the last parameter of `FetchAndPlace`.

- [ ] **Step 5: Update the caller**

In `QuestDemo/Assets/Spatial/CoordinatorClient.cs` near line 439, the `place_generated` branch must pass the store. The client already holds one at line 121 as `internal DrawingStore Store;`, so replace the branch with:

```csharp
            if (op.Kind == "place_generated")
            {
                if (!PrepareSceneOp(op))
                    return;
                if (Store == null)
                {
                    Debug.LogWarning("CoordinatorClient: no DrawingStore for place_generated");
                    return;
                }
                GeneratedMeshPlacer.TryHandle(this, this, op, _ipv4, _artifactPort, Cache, Store);
                return;
            }
```

Keep the existing `PrepareSceneOp(op)` gate: every other op branch calls it, and skipping it would let an unprepared op through. Confirm `Store` is assigned by `SpatialRuntime` before a session starts; if it is not, assign it where the other drawables are wired.

Add `NotifyMeshMissing` to `CoordinatorClient` so the mesh-absent path is observable and logged without inventing a protocol message:

```csharp
    /// <summary>
    /// The box is placed; the mesh never arrived. Quest sends no arrival
    /// report (spec §8.2), so this only logs: the coordinator must not be
    /// told a transfer completed when it did not.
    /// </summary>
    internal void NotifyMeshMissing(string drawingId)
    {
        Debug.Log("GENERATED_MESH_MISSING drawing=" + drawingId);
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter GeneratedMeshPlacerTests -testResults /tmp/gen-green.xml -logFile /tmp/unity-gen-green.log'`
Expected: PASS, 9/9.

Then run the whole EditMode suite to catch store regressions:

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testResults /tmp/all-editmode.xml -logFile /tmp/unity-all.log'`
Expected: PASS. Confirm `result="Passed"` in `/tmp/all-editmode.xml`.

- [ ] **Step 7: Commit**

```bash
git add QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs QuestDemo/Assets/Spatial/DrawingStore.cs QuestDemo/Assets/Spatial/CoordinatorClient.cs QuestDemo/Assets/Tests/Editor/GeneratedMeshPlacerTests.cs
git commit -m "Quest: generated furniture fits its listed box and joins DrawingStore"
```

---

## Task 11: Revise generated furniture by voice

**Files:**
- Modify: `QuestDemo/Assets/Spatial/DrawingStore.cs`
- Create: `QuestDemo/Assets/Tests/Editor/GeneratedRevisionTests.cs`

**Interfaces:**
- Consumes: `_generated` from Task 10.
- Produces: `DrawingStore.ApplyGeneratedRevision(string drawingId, string action, string direction, out string error) -> bool`.

**Rule:** nudge, rotate, and remove apply. `enlarge` and `shrink` are refused — the listing is the truth, and letting voice resize generated furniture would silently break the size claim.

- [ ] **Step 1: Write the failing test**

Create `QuestDemo/Assets/Tests/Editor/GeneratedRevisionTests.cs`:

```csharp
using NUnit.Framework;
using UnityEngine;

public class GeneratedRevisionTests
{
    DrawingStore _store;

    [SetUp]
    public void SetUp()
    {
        _store = new GameObject("store").AddComponent<DrawingStore>();
    }

    [TearDown]
    public void TearDown()
    {
        Object.DestroyImmediate(_store.gameObject);
    }

    GameObject Place(string id)
    {
        return _store.PlaceGenerated(
            Vector3.zero, Vector3.up, id, new Vector3(0.55f, 0.40f, 0.72f));
    }

    [Test]
    public void NudgeMovesTheWholePlacement()
    {
        Place("d1");
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "nudge", "right", out error));
        Assert.IsNull(error);
        Assert.Greater(_store.transform.Find("Drawing_d1"), null);
    }

    [Test]
    public void RotateIsAllowed()
    {
        Place("d1");
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "rotate_cw", null, out error));
    }

    [Test]
    public void EnlargeIsRefusedBecauseTheListingIsTruth()
    {
        Place("d1");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d1", "enlarge", null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void ShrinkIsRefusedBecauseTheListingIsTruth()
    {
        Place("d1");
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("d1", "shrink", null, out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void NudgeOnAnUnknownDrawingIsRefused()
    {
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("nope", "nudge", "left", out error));
        Assert.AreEqual("invalid", error);
    }

    [Test]
    public void RemoveDeletesTheGeneratedDrawing()
    {
        Place("d1");
        string error;
        Assert.IsTrue(_store.ApplyGeneratedRevision("d1", "remove", null, out error));
        Assert.IsFalse(_store.HasGenerated("d1"));
        Assert.AreEqual(0, _store.Count);
    }

    [Test]
    public void RemovingAnUnknownDrawingIsRefused()
    {
        string error;
        Assert.IsFalse(_store.ApplyGeneratedRevision("nope", "remove", null, out error));
        Assert.AreEqual("invalid", error);
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter GeneratedRevisionTests -testResults /tmp/rev-red.xml -logFile /tmp/unity-rev-red.log'`
Expected: FAIL to compile — no `ApplyGeneratedRevision`.

- [ ] **Step 3: Implement**

In `QuestDemo/Assets/Spatial/DrawingStore.cs`, add:

```csharp
    /// <summary>
    /// Revise one generated placement. Nudge, rotate, and remove apply;
    /// enlarge and shrink are refused because the listed size is the claim
    /// (spec §7.3) and voice must not quietly break it.
    /// </summary>
    public bool ApplyGeneratedRevision(
        string drawingId, string action, string direction, out string error)
    {
        error = null;
        GeneratedRecord rec = null;
        if (string.IsNullOrEmpty(drawingId) || !_generated.TryGetValue(drawingId, out rec))
        {
            error = "invalid";
            return false;
        }
        if (rec.Root == null)
        {
            _generated.Remove(drawingId);
            error = "invalid";
            return false;
        }
        if (action == "remove")
        {
            _generated.Remove(drawingId);
            _marks.Remove(rec.Root);
            DestroyMark(rec.Root);
            return true;
        }
        if (action == "enlarge" || action == "shrink")
        {
            error = "invalid";
            return false;
        }
        Transform t = rec.Root.transform;
        if (action == "rotate_cw" || action == "rotate_ccw")
        {
            float step = action == "rotate_cw"
                ? -ProceduralFactory.RotateStepDeg
                : ProceduralFactory.RotateStepDeg;
            t.Rotate(Vector3.up, step, Space.Self);
            return true;
        }
        if (action == "nudge")
        {
            Vector3 dir;
            Vector3 flat = new Vector3(t.forward.x, 0f, t.forward.z);
            if (flat.sqrMagnitude < 1e-6f)
                flat = Vector3.forward;
            Transform frame = t;
            frame.rotation = Quaternion.LookRotation(flat.normalized, Vector3.up);
            if (!NudgeDirection(direction, frame, out dir))
            {
                error = "invalid";
                return false;
            }
            // Floor plane only: generated furniture is not lifted.
            dir.y = 0f;
            t.position += dir.normalized * ProceduralFactory.NudgeStepM;
            return true;
        }
        error = "invalid";
        return false;
    }
```

- [ ] **Step 4: Route revise ops to it**

In `QuestDemo/Assets/Spatial/CoordinatorClient.cs`, in the `revise_procedural` dispatch near line 453, try the generated store first and fall back to the procedural path:

```csharp
            if (op.Kind == "place_procedural" || op.Kind == "revise_procedural")
            {
                if (!PrepareSceneOp(op))
                    return;
                if (op.Kind == "revise_procedural" && Store != null &&
                    Store.HasGenerated(op.DrawingId))
                {
                    string genError;
                    bool genOk = Store.ApplyGeneratedRevision(
                        op.DrawingId, op.Action, op.Direction, out genError);
                    EnqueueAck(op, genOk ? "applied" : "rejected", op.DrawingId,
                        genOk ? null : genError, null);
                    return;
                }
                ProceduralFactory.TryHandle(this, op);
                return;
            }
```

The ACK helper is `internal void EnqueueAck(SceneOpMsg op, string status, string drawingId, string reason, string pin)` (line 720). An `applied` ACK carries the `drawing_id` and no reason; a refusal carries the reason and a null drawing id, matching how `place_generated` already refuses.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter GeneratedRevisionTests -testResults /tmp/rev-green.xml -logFile /tmp/unity-rev-green.log'`
Expected: PASS, 7/7.

- [ ] **Step 6: Commit**

```bash
git add QuestDemo/Assets/Spatial/DrawingStore.cs QuestDemo/Assets/Spatial/CoordinatorClient.cs QuestDemo/Assets/Tests/Editor/GeneratedRevisionTests.cs
git commit -m "Quest: revise generated furniture without breaking listed size"
```

---

## Task 12: Floor-plane grab

**Files:**
- Create: `QuestDemo/Assets/Spatial/FloorPlaneGrab.cs`
- Create: `QuestDemo/Assets/Tests/Editor/FloorPlaneGrabTests.cs`
- Modify: `QuestDemo/Assets/Spatial/Omni.Spatial.asmdef`

**Interfaces:**
- Consumes: the generated root from `DrawingStore.PlaceGenerated` (Task 10).
- Produces: `FloorPlaneGrab.ConstrainPosition(Vector3 desired, float lockedY) -> Vector3` and `FloorPlaneGrab.ConstrainedScale(Vector3 current, Vector3 atGrabStart) -> Vector3`.

**Why the asmdef change:** `Omni.Spatial` does not currently reference `Unity.XR.Interaction.Toolkit`, so `GrabInteractable` will not compile until it does.

- [ ] **Step 1: Write the failing test**

Create `QuestDemo/Assets/Tests/Editor/FloorPlaneGrabTests.cs`:

```csharp
using NUnit.Framework;
using UnityEngine;

public class FloorPlaneGrabTests
{
    [Test]
    public void ConstrainKeepsTheLockedHeightAndFollowsXZ()
    {
        Vector3 desired = new Vector3(1.2f, 3.5f, -0.4f);
        Vector3 constrained = FloorPlaneGrab.ConstrainPosition(desired, 0f);
        Assert.AreEqual(1.2f, constrained.x, 1e-5f);
        Assert.AreEqual(0f, constrained.y, 1e-5f);
        Assert.AreEqual(-0.4f, constrained.z, 1e-5f);
    }

    [Test]
    public void ConstrainHonoursANonZeroSurfaceHeight()
    {
        Vector3 constrained = FloorPlaneGrab.ConstrainPosition(new Vector3(0f, -9f, 0f), 0.75f);
        Assert.AreEqual(0.75f, constrained.y, 1e-5f);
    }

    [Test]
    public void ScaleIsLockedToTheValueAtGrabStart()
    {
        Vector3 atGrab = new Vector3(0.55f, 0.72f, 0.40f);
        Vector3 current = new Vector3(0.90f, 1.20f, 0.10f);
        Vector3 result = FloorPlaneGrab.ConstrainedScale(current, atGrab);
        Assert.AreEqual(atGrab.x, result.x, 1e-5f);
        Assert.AreEqual(atGrab.y, result.y, 1e-5f);
        Assert.AreEqual(atGrab.z, result.z, 1e-5f);
    }

    [Test]
    public void GrabDisablesEngineDrivenPositionAndScale()
    {
        var go = new GameObject("grabbable", typeof(RectTransform));
        var grab = go.AddComponent<UnityEngine.XR.Interaction.Toolkit.GrabInteractable>();
        FloorPlaneGrab.Configure(grab);
        Assert.IsFalse(grab.trackPosition, "we move it ourselves on the floor");
        Assert.IsFalse(grab.trackRotation, "yaw is voice-only in this slice");
        Assert.IsFalse(grab.trackScale, "listed size must survive a grab");
        Object.DestroyImmediate(go);
    }

    [Test]
    public void AttachAddsAGrabInteractableAndTheConstraint()
    {
        var go = new GameObject("drawing");
        FloorPlaneGrab attached = FloorPlaneGrab.Attach(go, 0.25f);
        Assert.IsNotNull(attached);
        Assert.IsNotNull(go.GetComponent<UnityEngine.XR.Interaction.Toolkit.GrabInteractable>());
        Assert.AreEqual(0.25f, attached.LockedY, 1e-5f);
        Object.DestroyImmediate(go);
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter FloorPlaneGrabTests -testResults /tmp/grab-red.xml -logFile /tmp/unity-grab-red.log'`
Expected: FAIL to compile — no `FloorPlaneGrab`.

- [ ] **Step 3: Add the package reference**

In `QuestDemo/Assets/Spatial/Omni.Spatial.asmdef`, add the toolkit to `references`:

```json
{
  "name": "Omni.Spatial",
  "references": ["meta.xr.mrutilitykit", "Oculus.VR", "Omni.Voice", "glTFast", "Unity.Collections", "Unity.XR.Interaction.Toolkit"]
}
```

Confirm the package is installed (`com.unity.xr.interaction.toolkit` is `3.6.1` in `QuestDemo/Packages/manifest.json`). If Unity reports the assembly name is different, read the exact `name` from the package's own asmdef under `Library/PackageCache/com.unity.xr.interaction.toolkit*/` and use that string.

- [ ] **Step 4: Write the implementation**

Create `QuestDemo/Assets/Spatial/FloorPlaneGrab.cs`:

```csharp
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

/// <summary>
/// Floor-plane drag for generated furniture (spec §7.2). X and Z follow the
/// hand; Y stays locked to the surface the object was planted on, and scale
/// stays locked to the listed size. Rotation is voice-only in this slice, so
/// the engine's own tracking is switched off and this component moves the
/// object instead. The constraint math is static and pure for EditMode tests.
/// </summary>
public class FloorPlaneGrab : MonoBehaviour
{
    public float LockedY;

    Vector3 _scaleAtGrabStart;
    bool _held;

    /// <summary>Desired position clamped to the locked height.</summary>
    public static Vector3 ConstrainPosition(Vector3 desired, float lockedY)
    {
        return new Vector3(desired.x, lockedY, desired.z);
    }

    /// <summary>Scale never changes during a grab.</summary>
    public static Vector3 ConstrainedScale(Vector3 current, Vector3 atGrabStart)
    {
        return atGrabStart;
    }

    /// <summary>Switch off engine-driven motion; this component owns it.</summary>
    public static void Configure(GrabInteractable grab)
    {
        if (grab == null)
            return;
        grab.trackPosition = false;
        grab.trackRotation = false;
        grab.trackScale = false;
    }

    /// <summary>Attach a grabbable to a generated root. Returns the constraint.</summary>
    public static FloorPlaneGrab Attach(GameObject root, float lockedY)
    {
        if (root == null)
            return null;
        GrabInteractable grab = root.GetComponent<GrabInteractable>();
        if (grab == null)
            grab = root.AddComponent<GrabInteractable>();
        Configure(grab);
        FloorPlaneGrab constraint = root.GetComponent<FloorPlaneGrab>();
        if (constraint == null)
            constraint = root.AddComponent<FloorPlaneGrab>();
        constraint.LockedY = lockedY;
        return constraint;
    }

    void OnEnable()
    {
        GrabInteractable grab = GetComponent<GrabInteractable>();
        if (grab == null)
            return;
        grab.selectEntered.AddListener(OnGrabbed);
        grab.selectExited.AddListener(OnReleased);
    }

    void OnDisable()
    {
        GrabInteractable grab = GetComponent<GrabInteractable>();
        if (grab == null)
            return;
        grab.selectEntered.RemoveListener(OnGrabbed);
        grab.selectExited.RemoveListener(OnReleased);
    }

    void OnGrabbed(SelectEnterEventArgs args)
    {
        _held = true;
        _scaleAtGrabStart = transform.localScale;
    }

    void OnReleased(SelectExitEventArgs args)
    {
        _held = false;
        // The pose it was left in is the pose it keeps: world-locked, no snap.
        transform.position = ConstrainPosition(transform.position, LockedY);
        transform.localScale = ConstrainedScale(transform.localScale, _scaleAtGrabStart);
    }

    void Update()
    {
        if (!_held)
            return;
        transform.position = ConstrainPosition(transform.position, LockedY);
        transform.localScale = ConstrainedScale(transform.localScale, _scaleAtGrabStart);
    }
}
```

If the installed XRI version names the events differently (`selectEntered` / `selectExited` are the XRI 3.x names), read the `GrabInteractable` and `XRBaseInteractable` source in `Library/PackageCache` and use the actual member names and event argument types. Do not leave this step unverified — the test in Step 1 exercises `Configure`, `Attach`, and the two pure helpers, which is what the plan guarantees; the event wiring needs a device pass.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testFilter FloorPlaneGrabTests -testResults /tmp/grab-green.xml -logFile /tmp/unity-grab-green.log'`
Expected: PASS, 5/5.

- [ ] **Step 6: Attach the grab to generated roots**

In `QuestDemo/Assets/Spatial/DrawingStore.cs`, at the end of `PlaceGenerated` before `return root;`, add:

```csharp
        // Generated furniture is grabbable on the floor plane. Root and box
        // coincide, so the locked height is simply the root's own Y.
        FloorPlaneGrab.Attach(root, root.transform.position.y);
```

The root coincides with the box (Step 3), so a collider at the root's local zero wraps the box exactly:

```csharp
        BoxCollider body = root.GetComponent<BoxCollider>();
        if (body == null)
            body = root.AddComponent<BoxCollider>();
        body.size = boxSize;
        body.center = Vector3.zero;
```

Because the root carries the pose and the box is its child at local zero, locking the root's Y keeps the object on the surface. The `RootAndBoxCoincideSoAGrabCannotDoubleOffset` test in Task 10 pins that invariant.

- [ ] **Step 7: Run the whole EditMode suite**

Run: `nix run .# -- -c 'cd QuestDemo && $UNITY_EDITOR_6000 -batchmode -nographics -projectPath $(pwd) -runTests -testPlatform EditMode -assemblyNames Omni.Spatial.Editor.Tests -testResults /tmp/all-final.xml -logFile /tmp/unity-all-final.log'`
Expected: PASS with every pre-existing test still green.

- [ ] **Step 8: Commit**

```bash
git add QuestDemo/Assets/Spatial/FloorPlaneGrab.cs QuestDemo/Assets/Tests/Editor/FloorPlaneGrabTests.cs QuestDemo/Assets/Spatial/Omni.Spatial.asmdef QuestDemo/Assets/Spatial/DrawingStore.cs
git commit -m "Quest: floor-plane grab with locked height and listed scale"
```

---

## Task 13: Document the wire change

**Files:**
- Modify: `docs/omni-worker-tools.md`
- Modify: `docs/omni-spatial-loop.md`

**Interfaces:**
- Consumes: everything above.
- Produces: docs that match the shipped behaviour, per the repo's documentation procedure.

- [ ] **Step 1: Update the worker-tools contract**

In `docs/omni-worker-tools.md`, in the Contract list, add:

```markdown
- `place_listing(name, extent_m, target)` is a model tool. `extent_m` is the
  listing's own `[width, depth, height]` in metres and is **required**: the
  model may not place a listing it cannot size. The coordinator validates the
  metres, records the row, and authors the `place_generated` op, which carries
  `extent_m` when the size is known.
- A listing that is pre-baked in the coordinator registry is placed without
  queueing a worker. Generation is otherwise serialized: one worker job per
  session at a time.
```

- [ ] **Step 2: Update the spatial loop**

In `docs/omni-spatial-loop.md`, add a short section:

```markdown
## Sized placement (added 2026-09-20)

`place_generated` may carry `extent_m` (3 axes, 0.05–3.0 m). When it does,
Quest plants a listed-size box at the capture-time hit and ACKs `placed`
immediately — the box is real whether or not the mesh exists yet. The GLB is
fetched with a bounded retry (6 attempts, ~15 s) because the artifact server
only serves a job once it is `ready`. The mesh is fitted uniformly inside the
box; it is never stretched, and a mesh whose proportions miss the listing by
more than 25% keeps the box visible and is reported as approximate. If the
mesh never arrives, the box stays at full stated size.

Generated drawings are `DrawingStore` citizens: one `drawing_id` covers the
box and the mesh, so remove, undo, the clutter cap, voice revise, and
floor-plane grab all address one object. Voice cannot enlarge or shrink
generated furniture — the listed size is the claim.
```

- [ ] **Step 3: Commit**

```bash
git add docs/omni-worker-tools.md docs/omni-spatial-loop.md
git commit -m "Docs: sized generated placement contract"
```

---

## Device gate (manual, after Task 12)

Not automatable. Do all of these on a real Quest 3S before calling the slice done.

1. Put a test GLB at `provider/artifacts/generated/<ULID>.glb` and a registry entry pointing at it. Place that listing and confirm the box appears at the stated size and the mesh lands inside it.
2. Tape a tape-measure against the box and confirm the measured width, height, and depth match `extent_m` within a centimetre or two.
3. Grab the object and drag it across the floor: it must not lift, must not resize, and must keep its final pose on release.
4. Say "make it bigger" and confirm it is refused rather than resized.
5. Pull the GLB away and place again: the box must stay at full listed size, and `GENERATED_MESH_MISSING` must appear in `adb logcat`.
6. Point the headset at a page stating sizes and confirm the model states those numbers back before anything is planted.
