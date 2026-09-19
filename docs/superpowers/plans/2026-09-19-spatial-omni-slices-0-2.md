# Spatial omni slices 0-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the Quest-laptop JSON contract, plant a pulsing world-locked ring from a capture-time pose on Quest 3S, then send one laptop-authored `mark` over LAN and ACK it, with no yibuapi call.

**Architecture:** Python owns schemas, fixtures, and a WebSocket coordinator on `ws://0.0.0.0:8765`. Unity in `QuestDemo/` captures a `CaptureEnvelope` from MRUK `PassthroughCameraAccess` (left camera), raycasts with `EnvironmentRaycastManager` at capture time, and renders a pulsing ring on an XR anchor. Slice 1 works with no laptop. Slice 2 adds hello/ping, one hardcoded `scene_op`, and `PlacementAck`. The model never emits world coordinates. Drawings stay on Quest if the socket dies.

**Tech Stack:** Unity 6 + Meta XR Core/MRUK 205 (`PassthroughCameraAccess`, `EnvironmentRaycastManager`, `OVRSpatialAnchor`), C# `ClientWebSocket`, Python 3 `unittest` + `jsonschema` + `websockets` (already in `provider/requirements.txt` except `jsonschema`).

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`. If this plan and the spec disagree, stop and fix the plan; do not invent a third protocol.
- Do not implement slices 3-6 (omni tool loop, barge-in tombstones as a demo, `connect`/`ghost` rotate-slide, grounder, `place_generated`).
- Do not call yibuapi. Do not put `YIBU_API_KEY` in Unity, logs, or the repo. Live credit expires **Sep 20, 2026 8:00 AM EDT**; that smoke is a later plan.
- Canonical camera is **left**. World frame `openxr_floor_stage`: Unity left-handed, +X right, +Y up, +Z forward, metres, quaternions `xyzw`.
- Pixel origin in the spec is **top-left**. MRUK `ViewportPointToRay` uses **bottom-left** viewport `(0,0)`. Convert; do not mix them.
- `pose` on the envelope is left-camera to stage. Delayed work uses capture-time pose, not current head pose. Do not confirm an old ray with a new head-pose depth sample.
- `world_hint` is one capture-time hit (`source` `pointing` or `centre`). A centre hint does not validate a later `image_point` on a different pixel. Stale (0.12 m) compares **the same ray** only.
- New `mark` defaults to `motion.kind` `pulse` at `period_s` 1.2. Unity runs motion at headset framerate.
- Validation: surface hit max 4 m, min 0.25 m. Honesty copy: `no_surface` → "I can't plant that on a surface."; `stale` → "That moved, look again."; `too_close` → "Too close for depth."; `offline` → "Laptop not connected." Never float a pin to hide a miss.
- IDs are lowercase ULIDs except `turn_id` (monotonic uint64 from 1) and `stage_epoch` (int, starts at 1). Duplicate `op_id` is ignored.
- Transport: one WebSocket, JSON text frames, no TLS. Unity never HTTP POSTs to yibuapi. Laptop binds `ws://0.0.0.0:8765`.
- Send priority, high to low: `cancel`, `stop_speak`, `ack`, `turn_started`, `scene_op`, `speak`, `honesty`, `clear_session`, `pong`/`ping`, `audio_chunk`, `frame`. Do not block `cancel`/`ack` behind a JPEG.
- Existing `QuestDemo/` is the Unity app. Do not create a second Unity project. Extend `ARRuntime` / `ARSetup`; keep `DemoCube` if present, do not use it as the spatial pin.
- Offline Python: `cd provider && . .venv/bin/activate && python -m unittest discover -s tests -v`. Do not "fix" vendored yibu example files for linters.
- User git rule: do not `git commit` unless the user explicitly asked. Commit steps below are for later; skip them until then.
- JPEG for later slices: quality 70, max 350 KB `jpeg_b64`. Slice 1 may log envelopes without sending images. Slice 2 may send a small JPEG or omit `jpeg_b64` if the first `frame` is envelope-only; coordinator still needs `frame_id` on the envelope.

## File map

| Path | Role |
| --- | --- |
| `provider/protocol/schemas/message.json` | Wrapper `{v, type, session_id, turn_id, utterance_id, payload}` |
| `provider/protocol/schemas/capture_envelope.json` | CaptureEnvelope |
| `provider/protocol/schemas/scene_op.json` | SceneOp sent laptop → Quest (may include Quest-only `world_point` after resolve; coordinator must not fill metres from the model) |
| `provider/protocol/schemas/model_scene_op.json` | ModelSceneOp: no metres, no `world_point` |
| `provider/protocol/schemas/placement_ack.json` | PlacementAck |
| `provider/protocol/fixtures/valid/` | Hand-written passing examples |
| `provider/protocol/fixtures/invalid/` | Hand-written rejects |
| `provider/protocol/validate.py` | Load schemas, `validate_instance(schema_name, data)` |
| `provider/protocol/ids.py` | `new_ulid() -> str` |
| `provider/protocol/uv.py` | JPEG `u,v` (spec) ↔ PCA viewport |
| `provider/protocol/stale.py` | Same-ray 0.12 m stale rule |
| `provider/tests/test_protocol.py` | Schema + uv + stale + wrap tests |
| `provider/coordinator/server.py` | `ws://0.0.0.0:8765` hello/ping/hardcoded mark |
| `provider/coordinator/session.py` | Session id, stage_epoch fence, last envelope |
| `provider/tests/test_coordinator.py` | In-process WebSocket client tests |
| `provider/requirements.txt` | Add `jsonschema>=4,<5` |
| `QuestDemo/Assets/Spatial/CaptureEnvelope.cs` | Envelope struct + JSON |
| `QuestDemo/Assets/Spatial/CaptureGeometryCache.cs` | 10 s per-`frame_id` cache |
| `QuestDemo/Assets/Spatial/PlacementResolver.cs` | Raycast, pin, stale, honesty reasons |
| `QuestDemo/Assets/Spatial/PulsingRing.cs` | 0.06 m ring, pulse 1.2 s |
| `QuestDemo/Assets/Spatial/HonestyChip.cs` | Near-reticle chip |
| `QuestDemo/Assets/Spatial/CoordinatorClient.cs` | WebSocket client, send priority |
| `QuestDemo/Assets/Spatial/ProtocolJson.cs` | Serialize/parse spec messages |
| `QuestDemo/Assets/Spatial/SpatialRuntime.cs` | Slice 1+2 director (PCA left, depth, talk trigger) |
| `QuestDemo/Assets/ARRuntime.cs` | Keep permission/quad; SpatialRuntime owns pin |
| `QuestDemo/Assets/Editor/ARSetup.cs` | Add `EnvironmentRaycastManager`, force PCA Left |
| `QuestDemo/Assets/Tests/Editor/UvConventionTests.cs` | Same numbers as `uv.py` |
| `docs/omni-spatial-loop.md` | What it is, contract, how to verify (index in AGENTS.md) |
| `AGENTS.md` | One Layout line for the loop doc + plans path |

---

### Task 1: Freeze JSON Schema and fixtures

**Files:**
- Create: `provider/protocol/schemas/message.json`
- Create: `provider/protocol/schemas/capture_envelope.json`
- Create: `provider/protocol/schemas/scene_op.json`
- Create: `provider/protocol/schemas/model_scene_op.json`
- Create: `provider/protocol/schemas/placement_ack.json`
- Create: `provider/protocol/validate.py`
- Create: `provider/protocol/ids.py`
- Create: `provider/protocol/__init__.py` (empty)
- Create: `provider/protocol/fixtures/valid/hello.json`
- Create: `provider/protocol/fixtures/valid/capture_envelope.json`
- Create: `provider/protocol/fixtures/valid/scene_op_mark.json`
- Create: `provider/protocol/fixtures/valid/placement_ack_placed.json`
- Create: `provider/protocol/fixtures/invalid/model_world_point.json`
- Create: `provider/protocol/fixtures/invalid/mark_missing_target.json`
- Create: `provider/tests/test_protocol.py`
- Modify: `provider/requirements.txt` (append `jsonschema>=4,<5`)

**Interfaces:**
- Consumes: spec §3-§8 field names.
- Produces: `validate_instance(name: str, data: object) -> None` raises `jsonschema.ValidationError`; `load_fixture(kind: str, filename: str) -> dict`; `new_ulid() -> str` (26-char Crockford, lowercase).

- [ ] **Step 1: Write the failing test**

Create `provider/tests/test_protocol.py`:

```python
from __future__ import annotations

import unittest
from jsonschema import ValidationError

from protocol.validate import load_fixture, validate_instance
from protocol.ids import new_ulid


class SchemaTests(unittest.TestCase):
    def test_valid_capture_envelope(self) -> None:
        data = load_fixture("valid", "capture_envelope.json")
        validate_instance("capture_envelope", data)

    def test_valid_hello_wrapper(self) -> None:
        data = load_fixture("valid", "hello.json")
        validate_instance("message", data)

    def test_valid_mark(self) -> None:
        data = load_fixture("valid", "scene_op_mark.json")
        validate_instance("scene_op", data)

    def test_valid_ack(self) -> None:
        data = load_fixture("valid", "placement_ack_placed.json")
        validate_instance("placement_ack", data)

    def test_model_must_not_emit_world_point(self) -> None:
        data = load_fixture("invalid", "model_world_point.json")
        with self.assertRaises(ValidationError):
            validate_instance("model_scene_op", data)

    def test_mark_without_target_rejected(self) -> None:
        data = load_fixture("invalid", "mark_missing_target.json")
        with self.assertRaises(ValidationError):
            validate_instance("scene_op", data)

    def test_ulid_lowercase_26(self) -> None:
        value = new_ulid()
        self.assertEqual(len(value), 26)
        self.assertEqual(value, value.lower())
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/nate/Projects/omni/provider
. .venv/bin/activate
python -m unittest tests.test_protocol -v
```

Expected: `ModuleNotFoundError: No module named 'protocol'` (or `jsonschema`).

- [ ] **Step 3: Install jsonschema and write schemas + loader**

```bash
cd /home/nate/Projects/omni/provider
. .venv/bin/activate
python -m pip install 'jsonschema>=4,<5'
```

Append to `provider/requirements.txt`:

```
jsonschema>=4,<5
```

`provider/protocol/ids.py`:

```python
from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def new_ulid() -> str:
    ms = int(time.time() * 1000)
    time_chars = []
    for _ in range(10):
        time_chars.append(_CROCKFORD[ms % 32])
        ms //= 32
    rand = int.from_bytes(os.urandom(10), "big")
    rand_chars = []
    for _ in range(16):
        rand_chars.append(_CROCKFORD[rand % 32])
        rand //= 32
    return "".join(reversed(time_chars)) + "".join(reversed(rand_chars))
```

`provider/protocol/validate.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_ROOT = Path(__file__).resolve().parent
_SCHEMAS = _ROOT / "schemas"
_FIXTURES = _ROOT / "fixtures"

_CACHE: dict[str, Draft202012Validator] = {}


def _validator(name: str) -> Draft202012Validator:
    if name not in _CACHE:
        path = _SCHEMAS / f"{name}.json"
        schema = json.loads(path.read_text())
        _CACHE[name] = Draft202012Validator(schema)
    return _CACHE[name]


def validate_instance(name: str, data: Any) -> None:
    _validator(name).validate(data)


def load_fixture(kind: str, filename: str) -> Any:
    return json.loads((_FIXTURES / kind / filename).read_text())
```

Write `provider/protocol/schemas/message.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["v", "type", "session_id", "turn_id", "utterance_id", "payload"],
  "properties": {
    "v": { "const": 1 },
    "type": {
      "type": "string",
      "enum": [
        "hello", "hello_ok", "audio_chunk", "utterance_end", "frame", "ack",
        "cancel", "clear_session", "ping", "pong", "turn_started", "scene_op",
        "speak", "stop_speak", "request_frame", "honesty", "session_cleared"
      ]
    },
    "session_id": { "type": ["string", "null"] },
    "turn_id": { "type": "integer", "minimum": 0 },
    "utterance_id": { "type": ["string", "null"] },
    "payload": { "type": "object" }
  }
}
```

Write `provider/protocol/schemas/capture_envelope.json` with required `frame_id`, `stage_epoch`, `t_unix_ns`, `camera` const `"left"`, `image_w`, `image_h`, `sent_w`, `sent_h`, `intrinsics` `{fx,fy,cx,cy}` numbers, `distortion` `{model, k}`, `pose` `{px,py,pz,qx,qy,qz,qw,frame}` with `frame` const `openxr_floor_stage`, `crop` `{sx,sy,tx,ty}`, `pointing` null or object with `source` enum `head|controller|hand`, `world_hint` null or object with required `source` enum `pointing|centre`, `capture_geometry_available` boolean.

Write `provider/protocol/schemas/model_scene_op.json`: `kind` enum `mark|label|connect|ghost|place_known|place_generated|remove|undo`; `target.type` enum **only** `capture_hint|image_point|image_box|drawing|pointing` (no `world_point`); `ghost` requires `motion.kind` `rotate` or `slide`. No `px`/`py`/`pz` properties on the target.

Write `provider/protocol/schemas/scene_op.json`: same as model plus allowed `target.type` `world_point` with `px,py,pz,frame` and optional `nx,ny,nz,frame_id`; required `op_id`, `turn_id`, `stage_epoch`, `kind`; `drawing_id` string or null; `motion` object or null; default mark pulse documented in fixtures not in schema (schema allows omit or object).

Write `provider/protocol/schemas/placement_ack.json`: required `op_id`, `turn_id`, `stage_epoch`, `status` enum `placed|applied|rejected|stale`; `drawing_id` string or null; `reason` string or null; `pin` `surface` or null. If `status` is `placed`, `drawing_id` must be string and `pin` `surface`. If `rejected` or `stale`, `drawing_id` must be null and `reason` required enum `out_of_camera|too_small|too_close|no_surface|clutter|invalid|superseded|timeout`.

Fixtures (use real-looking lowercase 26-char ids, numbers as in spec examples):

`valid/hello.json`: message with `type` `hello`, `session_id` null, `turn_id` 0, payload `{ "device": "quest3s", "app": "QuestDemo", "os_version": "74", "capabilities": { "pca": true, "depth": true, "tts": true } }`.

`valid/capture_envelope.json`: left camera 1280x960, identity crop, `pointing` head, `world_hint` source `pointing`, `capture_geometry_available` true.

`valid/scene_op_mark.json`: `kind` `mark`, `target.type` `capture_hint`, `motion` `{ "kind": "pulse", "period_s": 1.2 }`.

`valid/placement_ack_placed.json`: `status` `placed`, `pin` `surface`.

`invalid/model_world_point.json`: ModelSceneOp `target.type` `world_point` with metres.

`invalid/mark_missing_target.json`: SceneOp `kind` `mark` with no `target`.

- [ ] **Step 4: Run tests**

```bash
cd /home/nate/Projects/omni/provider
. .venv/bin/activate
python -m unittest tests.test_protocol -v
python -m unittest discover -s tests -v
```

Expected: new schema tests PASS; existing 9 audit tests still PASS.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add provider/protocol provider/tests/test_protocol.py provider/requirements.txt
git commit -m "$(cat <<'EOF'
Freeze Quest-laptop JSON schemas and fixtures for slices 0-2.

EOF
)"
```

---

### Task 2: JPEG u,v (spec) versus PCA viewport

**Files:**
- Create: `provider/protocol/uv.py`
- Create: `provider/protocol/fixtures/uv_cases.json`
- Modify: `provider/tests/test_protocol.py`
- Create: `QuestDemo/Assets/Tests/Editor/UvConventionTests.cs`
- Create: `QuestDemo/Assets/Spatial/UvConvention.cs`

**Interfaces:**
- Consumes: spec §4: `u_jpg = u * sent_w - 0.5`; pixel origin top-left; PCA `ViewportPointToRay` bottom-left `(0,0)` top-right `(1,1)` (`PassthroughCameraAccess.cs` around the `ViewportPointToRay` docstring).
- Produces: `spec_uv_to_pca_viewport(u: float, v: float) -> tuple[float, float]` returns `(u, 1.0 - v)`; `spec_uv_to_pixel_center(u, v, sent_w, sent_h) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing Python test**

Add to `provider/tests/test_protocol.py`:

```python
from protocol.uv import spec_uv_to_pca_viewport, spec_uv_to_pixel_center


class UvTests(unittest.TestCase):
    def test_top_left_pixel_maps_to_pca_top(self) -> None:
        # spec v=0 is top of JPEG; PCA y=1 is top
        x, y = spec_uv_to_pca_viewport(0.0, 0.0)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 1.0)

    def test_bottom_right(self) -> None:
        x, y = spec_uv_to_pca_viewport(1.0, 1.0)
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, 0.0)

    def test_pixel_centre_formula(self) -> None:
        u_jpg, v_jpg = spec_uv_to_pixel_center(0.5, 0.25, 1280, 960)
        self.assertAlmostEqual(u_jpg, 0.5 * 1280 - 0.5)
        self.assertAlmostEqual(v_jpg, 0.25 * 960 - 0.5)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate && python -m unittest tests.test_protocol.UvTests -v
```

Expected: `ModuleNotFoundError: No module named 'protocol.uv'`.

- [ ] **Step 3: Implement `uv.py` and `UvConvention.cs`**

`provider/protocol/uv.py`:

```python
from __future__ import annotations


def spec_uv_to_pca_viewport(u: float, v: float) -> tuple[float, float]:
    return (u, 1.0 - v)


def spec_uv_to_pixel_center(u: float, v: float, sent_w: float, sent_h: float) -> tuple[float, float]:
    return (u * sent_w - 0.5, v * sent_h - 0.5)
```

`QuestDemo/Assets/Spatial/UvConvention.cs`:

```csharp
using UnityEngine;

public static class UvConvention
{
    public static Vector2 SpecUvToPcaViewport(float u, float v)
    {
        return new Vector2(u, 1f - v);
    }

    public static Vector2 SpecUvToPixelCenter(float u, float v, float sentW, float sentH)
    {
        return new Vector2(u * sentW - 0.5f, v * sentH - 0.5f);
    }
}
```

`QuestDemo/Assets/Tests/Editor/UvConventionTests.cs` (asmdef: Editor-only, references `com.unity.test-framework`):

```csharp
using NUnit.Framework;
using UnityEngine;

public class UvConventionTests
{
    [Test]
    public void TopLeftSpecMapsToPcaTop()
    {
        var p = UvConvention.SpecUvToPcaViewport(0f, 0f);
        Assert.AreEqual(0f, p.x, 1e-5f);
        Assert.AreEqual(1f, p.y, 1e-5f);
    }
}
```

If creating an asmdef is painful in headless Unity, keep the C# helper and skip the Editor test until someone opens the project; Python remains the gate.

- [ ] **Step 4: Run Python tests**

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate && python -m unittest tests.test_protocol.UvTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add provider/protocol/uv.py provider/tests/test_protocol.py QuestDemo/Assets/Spatial/UvConvention.cs QuestDemo/Assets/Tests
git commit -m "$(cat <<'EOF'
Document spec JPEG u,v versus MRUK bottom-left viewport.

EOF
)"
```

---

### Task 3: Same-ray stale rule

**Files:**
- Create: `provider/protocol/stale.py`
- Modify: `provider/tests/test_protocol.py`
- Create: `QuestDemo/Assets/Spatial/StaleMath.cs`

**Interfaces:**
- Consumes: spec §8: if delayed raycast from capture pose into **current** mesh is more than 0.12 m from the cached capture-time hit for **the same ray**, or there was no cached hit for that ray, status is `stale`. Do not compare an `image_point` hit to a centre/pointing `world_hint` from a different ray.
- Produces: `classify_hit(cached: tuple[float,float,float] | None, delayed: tuple[float,float,float] | None, same_ray: bool, threshold_m: float = 0.12) -> str` returning `"placed"`, `"stale"`, or `"no_surface"`.

- [ ] **Step 1: Write the failing test**

```python
from protocol.stale import classify_hit


class StaleTests(unittest.TestCase):
    def test_same_ray_far_is_stale(self) -> None:
        cached = (0.0, 0.8, 1.0)
        delayed = (0.2, 0.8, 1.0)  # 0.2 m
        self.assertEqual(classify_hit(cached, delayed, same_ray=True), "stale")

    def test_same_ray_close_is_placed(self) -> None:
        cached = (0.0, 0.8, 1.0)
        delayed = (0.05, 0.8, 1.0)
        self.assertEqual(classify_hit(cached, delayed, same_ray=True), "placed")

    def test_different_ray_ignores_centre_hint(self) -> None:
        centre = (0.0, 0.8, 1.0)
        other = (0.5, 0.8, 1.2)
        self.assertEqual(classify_hit(centre, other, same_ray=False), "placed")

    def test_no_delayed_hit(self) -> None:
        self.assertEqual(classify_hit((0, 0.8, 1), None, True), "no_surface")

    def test_no_cache_delayed_only_is_stale(self) -> None:
        self.assertEqual(classify_hit(None, (0, 0.8, 1), True), "stale")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate && python -m unittest tests.test_protocol.StaleTests -v
```

Expected: import error.

- [ ] **Step 3: Implement**

`provider/protocol/stale.py`:

```python
from __future__ import annotations

import math
from typing import Optional

Vec3 = tuple[float, float, float]


def _dist(a: Vec3, b: Vec3) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def classify_hit(
    cached: Optional[Vec3],
    delayed: Optional[Vec3],
    same_ray: bool,
    threshold_m: float = 0.12,
) -> str:
    if delayed is None:
        return "no_surface"
    if not same_ray:
        return "placed"
    if cached is None:
        return "stale"
    if _dist(cached, delayed) > threshold_m:
        return "stale"
    return "placed"
```

Mirror the same numbers in `QuestDemo/Assets/Spatial/StaleMath.cs` as `public const float ThresholdM = 0.12f` and `Classify(Vector3? cached, Vector3? delayed, bool sameRay)`.

- [ ] **Step 4: Run tests**

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate && python -m unittest tests.test_protocol.StaleTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add provider/protocol/stale.py provider/tests/test_protocol.py QuestDemo/Assets/Spatial/StaleMath.cs
git commit -m "$(cat <<'EOF'
Encode same-ray stale placement (0.12 m), not centre-hint identity.

EOF
)"
```

---

### Task 4: Slice 1 capture envelope on Quest (no LAN)

**Files:**
- Create: `QuestDemo/Assets/Spatial/CaptureEnvelope.cs`
- Create: `QuestDemo/Assets/Spatial/CaptureGeometryCache.cs`
- Create: `QuestDemo/Assets/Spatial/SpatialRuntime.cs`
- Modify: `QuestDemo/Assets/Editor/ARSetup.cs` (`EnsureAR`, remnants list, ARDirector components)
- Modify: `QuestDemo/Assets/ARRuntime.cs` (keep permission + optional quad; do not plant DemoCube as the pin)

**Interfaces:**
- Consumes: `Meta.XR.PassthroughCameraAccess` (`CameraPositionType.Left`, `RequestedResolution` 1280x960, `IsPlaying`, `Timestamp`, `CurrentResolution`, `Intrinsics.FocalLength/PrincipalPoint/SensorResolution`, `GetCameraPose()`, `GetTexture()`, `ViewportPointToRay(Vector2, Pose?)`). `Meta.XR.EnvironmentRaycastManager.Raycast(Ray, out EnvironmentRaycastHit, float maxDistance)`. `OVRManager.TrackingOriginChangePending` increments `stage_epoch`.
- Produces: `SpatialRuntime.TryCapture(out CaptureEnvelope env, out Pose cameraPose, out Ray ray)` using pointing if trigger held else image centre. Fills `world_hint` from that same ray. Cache key `frame_id` for 10 seconds.

**Approved correction (binding spec governs):** Configure a floor-based tracking origin before serializing `openxr_floor_stage` coordinates. Request spatial Scene permission in addition to camera permission; a fresh install must not wait forever for raycast data. When trigger pointing is unavailable, use the capture-time left-camera centre ray and serialize `pointing: null`, not a current head ray. For a trigger point, serialize the exact sampled controller ray and its sample time, and derive cache/hint from that same ray. Convert PCA sensor intrinsics to top-left original-texture pixel intrinsics using the SDK crop/scale semantics. Cache every valid capture for 10 s, including identity crop and explicit optional-hit state on misses/too-close frames. Do not fabricate a normal if the SDK does not supply one. Preserve all 80 random bits in Unity ULIDs. Render a visible runtime thin aim ray rather than relying on `Debug.DrawRay`.

- [ ] **Step 1: Write a failing Editor test for envelope JSON shape**

`QuestDemo/Assets/Tests/Editor/CaptureEnvelopeJsonTests.cs` asserts a hand-built envelope serializes `camera` as `left` and `pose.frame` as `openxr_floor_stage`. If EditMode tests are not wired, write the serializer first and verify with a `Debug.Log` of one envelope on device (slice 1 pass/fail is on-headset).

Python already validates the fixture; Unity must emit the same keys. Add `QuestDemo/Assets/Spatial/ProtocolJson.cs` with explicit `JsonUtility`-friendly DTOs **plus** a `ToSpecJson()` that emits `null` for missing pointing using `Newtonsoft.Json` **or** a small handwritten writer. Prefer handwritten `StringBuilder` JSON for `null` support so we do not add a Unity package in this slice unless already present (manifest has no Newtonsoft).

Minimum `ToSpecJson` fields: every key in `valid/capture_envelope.json`.

- [ ] **Step 2: Confirm the test harness**

If EditMode tests cannot run headless, skip to device log check: one line `ENVELOPE {json}` that `python -c` can pipe into `validate_instance("capture_envelope", ...)`.

- [ ] **Step 3: Implement capture**

`ARSetup.EnsureAR` changes:
- Add `"EnvironmentDepth"` and `"EnvRaycast"` to `k_Remnants` if you name those objects.
- On `ARDirector`: `PassthroughCameraAccess.CameraPosition = Left`, `RequestedResolution = (1280, 960)`.
- Add `EnvironmentDepthManager` (namespace `Meta.XR.EnvironmentDepth`) and `EnvironmentRaycastManager` to `ARDirector` or a child. Depth raycast is not ready for several frames; `SpatialRuntime` must wait for `EnvironmentRaycastManager` ready (`IsSupported` and a successful `Raycast` or documented `IsReady` if public).
- Add `SpatialRuntime` component next to `ARRuntime`.

`CaptureGeometryCache`: dictionary `frame_id -> {pose, intrinsics, crop, ray origin/dir, hit point, t_unix_ns}` with 10 s TTL. `Available(frameId)` drives `capture_geometry_available`.

`SpatialRuntime` pointing (slice 1):
- Prefer right controller aim (`OVRInput.Get(OVRInput.Button.PrimaryIndexTrigger, OVRInput.Controller.RTouch)` or XR Toolkit equivalent already in the project). Fallback: `CenterEyeAnchor` forward (head). Show a thin debug ray. Head reticle is not eye gaze.
- `t_unix_ns`: `(long)(pca.Timestamp.ToUniversalTime() - DateTime.UnixEpoch).Ticks * 100` (DateTime ticks are 100 ns; spec wants Unix ns). Double-check: `pca.Timestamp` is UnixEpoch + microseconds*10 ticks; convert with `new DateTimeOffset(pca.Timestamp.ToUniversalTime()).ToUnixTimeMilliseconds() * 1_000_000L` or from ticks: `(pca.Timestamp.Ticks - DateTime.UnixEpoch.Ticks) * 100`.
- Raycast: `new Ray(origin, direction)`, `maxDistance: 4f`. If hit distance `< 0.25f`, honesty `too_close`, do not pin. If no hit / `NoHit` / `NotReady`, honesty `no_surface`.
- `stage_epoch`: start 1; increment on `OVRManager.TrackingOriginChangePending`.

Do not send WebSocket yet. Log envelope JSON once per trigger press.

- [ ] **Step 4: Headset check (slice 1 partial)**

Build with existing `BuildAndroid.Build`. On Quest 3S, grant `HEADSET_CAMERA`. Pull logcat for `ENVELOPE`. Copy one JSON into:

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate
python - <<'PY'
import json, sys
from protocol.validate import validate_instance
validate_instance("capture_envelope", json.loads(sys.stdin.read()))
print("ok")
PY
```

Paste the log JSON on stdin. Expected: `ok`. Fail if `camera` is not `left` or pose is missing.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add QuestDemo/Assets/Spatial QuestDemo/Assets/Editor/ARSetup.cs QuestDemo/Assets/ARRuntime.cs QuestDemo/Assets/Tests
git commit -m "$(cat <<'EOF'
Capture left-camera envelopes with capture-time pose and world_hint.

EOF
)"
```

---

### Task 5: Pulsing ring pin + forced miss

**Files:**
- Create: `QuestDemo/Assets/Spatial/PlacementResolver.cs`
- Create: `QuestDemo/Assets/Spatial/PulsingRing.cs`
- Create: `QuestDemo/Assets/Spatial/HonestyChip.cs`
- Create: `QuestDemo/Assets/Spatial/DrawingStore.cs`
- Modify: `QuestDemo/Assets/Spatial/SpatialRuntime.cs`

**Interfaces:**
- Consumes: `StaleMath`, `CaptureGeometryCache`, `EnvironmentRaycastManager`.
- Produces: `DrawingStore.PlaceMark(Vector3 point, Vector3 normal, string drawingId)` returns GameObject with `OVRSpatialAnchor` on a ring (default diameter 0.06 m, clamp 0.03-0.25). `PulsingRing` loops scale/alpha at 1.2 s. `DrawingStore.Clear()` for later `clear_session`. Cap 8 drawings.

- [ ] **Step 1: Write a failing EditMode test for pulse default**

If EditMode is unavailable, assert in `PulsingRing.Awake` that `periodS == 1.2f` when unset, and verify visually.

Python already has motion in the mark fixture. No extra Python test required.

- [ ] **Step 2: Run whatever test exists; expect missing type `PulsingRing`**

- [ ] **Step 3: Implement pin + honesty**

`PulsingRing`: torus or scaled cylinder ring, unlit cyan `#3DDCFF`, `period_s` 1.2, animate `localScale` and material alpha. Must not parent to the camera.

`PlacementResolver.TryPlaceFromCapture(...)`:
- Use cached hit if `capture_geometry_available` for that ray.
- Else delayed raycast from **cached camera pose** (not current head). Run `StaleMath.Classify`. On `stale`, show chip "That moved, look again." and **do not** instantiate a confident pin.
- On `no_surface`: "I can't plant that on a surface."
- On `too_close`: "Too close for depth."
- `HonestyChip`: world-locked, ~0.12 m in front of `CenterEyeAnchor`, not screen-centre HUD. Auto-hide after 3 s.

`SpatialRuntime` on trigger: capture → resolve → place pulse. Second binding (two-button or long-press): force miss by raycasting into empty sky / disabling depth briefly and confirm no floating sticker.

- [ ] **Step 4: Headset pass/fail (spec slice 1)**

On a real table, plant the ring, walk ~90 degrees. Pass: pulse stays on the table, not on the head, and keeps pulsing without another capture. Fail: stuck to camera, jumps when you stand still, or a frozen unmarked cube. Then force a miss: chip appears, no pin in mid-air.

XR Simulator does not support PCA. Link is OK for iteration; the pass/fail is the native build.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add QuestDemo/Assets/Spatial
git commit -m "$(cat <<'EOF'
Plant a pulsing surface ring from capture-time hits; refuse floating misses.

EOF
)"
```

---

### Task 6: Coordinator hello, ping, hardcoded mark

**Files:**
- Create: `provider/coordinator/__init__.py`
- Create: `provider/coordinator/session.py`
- Create: `provider/coordinator/server.py`
- Create: `provider/tests/test_coordinator.py`

**Interfaces:**
- Consumes: `protocol.validate`, `protocol.ids.new_ulid`, `websockets` (already in requirements).
- Produces: `async def run_server(host: str = "0.0.0.0", port: int = 8765) -> None`. On Quest `hello` (`session_id` null), reply `hello_ok` with new `session_id` and `laptop_t_unix_ns`. On `ping`, reply `pong`. After the first valid `frame`, send one `scene_op` `kind=mark` `target.type=capture_hint` `motion.pulse` with new `op_id`, `turn_id=1`, and the envelope stage epoch. The default coordinator never sends a production mark on `hello`; a future explicit `--plant-on-hello` test flag may do so only when it supplies a resolvable cached frame id. Do not call yibuapi. Log clock skew if `|quest - laptop| > 2s` as `clock_skew_ns`; do not rewrite poses.

- [ ] **Step 1: Write the failing test**

`provider/tests/test_coordinator.py` using `websockets` in-process:

```python
from __future__ import annotations

import asyncio
import json
import unittest

from coordinator.server import handle_connection, CoordinatorState
from protocol.validate import validate_instance


class CoordinatorTests(unittest.TestCase):
    def test_hello_ok_assigns_session(self) -> None:
        async def scenario() -> None:
            state = CoordinatorState()
            q = asyncio.Queue()

            class DummyWs:
                def __init__(self) -> None:
                    self.sent = []
                async def send(self, text: str) -> None:
                    self.sent.append(json.loads(text))
                async def recv(self) -> str:
                    return await q.get()

            ws = DummyWs()
            task = asyncio.create_task(handle_connection(ws, state))
            await q.put(json.dumps({
                "v": 1, "type": "hello", "session_id": None, "turn_id": 0,
                "utterance_id": None,
                "payload": {
                    "device": "quest3s", "app": "QuestDemo", "os_version": "74",
                    "capabilities": {"pca": True, "depth": True, "tts": True},
                },
            }))
            await asyncio.sleep(0.05)
            hello_ok = next(m for m in ws.sent if m["type"] == "hello_ok")
            validate_instance("message", hello_ok)
            self.assertIsInstance(hello_ok["payload"]["session_id"], str)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
```

Adapt `DummyWs` to whatever API `handle_connection` needs (`async for message in ws` vs `recv`). Keep the test as an in-process fake, not a bound port, so unittest stays offline.

Also test: after injecting a valid `frame` message whose payload.envelope matches the capture fixture, the next sent message is `type=scene_op` with `kind=mark`, `turn_id=1`, and `validate_instance("scene_op", payload)`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate && python -m unittest tests.test_coordinator -v
```

Expected: import error.

- [ ] **Step 3: Implement server**

`session.py`: `CoordinatorState` holds `session_id`, `turn_id`, `latest_stage_epoch`, `last_envelope`, `pending_ops: dict[str, dict]`. Discard incoming envelopes with `stage_epoch` older than latest.

`server.py`: parse wrapper; `validate_instance("message", obj)` on every inbound; on failure log and ignore (do not crash). Outbound messages always `v: 1`. `speak` is out of scope except you may skip it entirely in slice 2 (no model). Do not send `speak` that claims a mark before ACK (there is no speech path yet; do not add fake success audio).

CLI: `python -m coordinator.server` binds `8765`.

- [ ] **Step 4: Run tests**

```bash
cd /home/nate/Projects/omni/provider && . .venv/bin/activate && python -m unittest tests.test_coordinator tests.test_protocol -v
```

Expected: PASS.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add provider/coordinator provider/tests/test_coordinator.py
git commit -m "$(cat <<'EOF'
Add LAN coordinator hello/ping and one hardcoded mark, no model.

EOF
)"
```

---

### Task 7: Quest WebSocket client, ACK, send priority

**Files:**
- Create: `QuestDemo/Assets/Spatial/CoordinatorClient.cs`
- Modify: `QuestDemo/Assets/Spatial/SpatialRuntime.cs`
- Modify: `QuestDemo/Assets/Spatial/ProtocolJson.cs`
- Create: `provider/tests/test_priority.py` (or extend `test_coordinator.py`)

**Interfaces:**
- Consumes: Player pref `laptop_ipv4` (string, not a secret). `ClientWebSocket` to `ws://{ip}:8765`.
- Produces: Quest sends `hello` then `ping` every 5 s; on `scene_op` `mark`, run `PlacementResolver` using envelope cache / capture_hint; send `ack` within 500 ms of render-or-reject. On socket down: honesty `offline` "Laptop not connected.", keep existing rings, do not delete drawings. Reconnect every 2 s; new `session_id` from `hello_ok`; do not clear drawings.

- [ ] **Step 1: Write a failing coordinator test for ACK handling**

Extend `CoordinatorTests`: after sending `scene_op`, inject `ack` `status=placed`; state marks `op_id` complete. Inject `ack` `status=rejected` `reason=no_surface`; state does not retry the same `op_id`.

Add `test_cancel_is_processed_before_queued_frame`: queue a large fake `frame` then a `cancel`; handler must record cancel before finishing frame parse. Implementation: inbound loop reads one JSON text frame at a time (already true for WS text messages). **Do not** concatenate JPEG into the same JSON as control if it delays ACK: `frame.jpeg_b64` may be omitted in slice 2. If present, `ack`/`cancel` are separate WS messages; the test documents that the server processes messages in arrival order and Quest **sends** `cancel`/`ack` without waiting to finish encoding a JPEG (Quest-side queue: `ConcurrentQueue` with `cancel`/`ack` dequeued first).

- [ ] **Step 2: Run test; expect missing ACK bookkeeping**

- [ ] **Step 3: Implement client**

`CoordinatorClient`:
- Background thread or `async` + Unity main-thread pump.
- Outgoing priority queue: `cancel` = 0, `ack` = 2, `ping` = 8, `frame` = 9 (numbers from spec order).
- Parse `scene_op`; if `turn_id` is 0 or missing, reject `invalid`.
- `stage_epoch` mismatch with local epoch: ACK `superseded`.
- Duplicate `op_id`: ignore.
- Default omitted `motion` on `mark` to pulse 1.2 s.

`SpatialRuntime`: if pref empty, slice 1-only (no connect). If set, connect after PCA playing.

Do not implement `audio_chunk` yet (slice 3).

- [ ] **Step 4: Integration on LAN**

Laptop: `cd provider && . .venv/bin/activate && python -m coordinator.server`

Quest: set `laptop_ipv4` to the laptop LAN address. Plant via laptop mark after one envelope/`hello`. Pass: ring appears on the table and ACKs in coordinator logs (`ack status=placed`). Unplug Wi-Fi: rings remain, chip `offline`.

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add QuestDemo/Assets/Spatial provider/tests/test_coordinator.py provider/coordinator
git commit -m "$(cat <<'EOF'
Connect Quest to the coordinator: hardcoded mark, ACK, offline keeps pins.

EOF
)"
```

---

### Task 8: Topic doc and AGENTS index

**Files:**
- Create: `docs/omni-spatial-loop.md`
- Modify: `AGENTS.md` Layout list

**Interfaces:**
- Consumes: this plan's pass/fail lines and spec §14 slices 0-2.
- Produces: one topic doc: what it is, contract, how to verify. No secrets, no second copy of the full schema.

- [ ] **Step 1: Write `docs/omni-spatial-loop.md`**

Contents (keep short):
- Headset captures left PCA + pointing; laptop sends scene ops; Unity ACKs; no yibu in this slice.
- Pointers: spec path, `provider/protocol/schemas/`, coordinator `python -m coordinator.server`.
- Verify: Python `unittest discover -s tests -v`; headset walk-around pulse; LAN mark + ACK; miss does not float.

- [ ] **Step 2: Add Layout lines in `AGENTS.md`**

After the existing `docs/` bullet, add:
- `docs/omni-spatial-loop.md`: Quest-laptop drawing loop (slices 0-2).
- Plans: `docs/superpowers/plans/`.

Do not paste schemas into AGENTS.md.

- [ ] **Step 3: Read-through**

Confirm the doc does not mention API keys or tell Unity to POST to yibuapi.

- [ ] **Step 4: No extra tests**

- [ ] **Step 5: Commit (only if the user asked)**

```bash
git add docs/omni-spatial-loop.md AGENTS.md
git commit -m "$(cat <<'EOF'
Index the spatial drawing loop and slice 0-2 verify steps.

EOF
)"
```

---

## Out of scope (later plans)

- Slice 3: PCM + JPEG to `qwen3.8-omni-flash`, `emit_scene_ops`, ACK then speak. Do a tiny yibu smoke with existing `qwen38_omni_flash.py` **before** that plan if the key is still live.
- Slice 4: barge-in tombstones, last-writer-wins, motion revise.
- Slice 5: `connect`/`travel`, `ghost` rotate/slide.
- Slice 6: grounder / LocateAnything license / TripoSR on either 8GB card.

## Self-review

**Spec coverage (slices 0-2):**
- §3 IDs/clocks: Task 1 (`new_ulid`), Task 4 (`stage_epoch`, `t_unix_ns`), Task 6 (session, skew log).
- §4 coordinates + PCA viewport mismatch: Task 2.
- §5 envelope + world_hint + 10 s cache: Tasks 1, 4, 5.
- §6 transport hello/ping/scene_op/ack, no TLS, no Unity yibu, send priority: Tasks 6-7. `audio_chunk` / `speak` / `clear_session` deferred except offline honesty.
- §7 ACK statuses placed/rejected/stale, no success speech before ACK: Task 7 (no speak). Full `ops_closed` HTTP wait is slice 3.
- §8 mark + default pulse, clutter cap, stale same-ray, model vs scene op: Tasks 1, 3, 5, 6.
- §9 honesty strings used in Task 5-7.
- §12 PCA left, depth, native build, no XR Simulator: Tasks 4-5.
- §13 recordings gitignored; slice 2 must not write JPEG to git (`provider/artifacts/` already ignored). Do not add a committed dump folder.
- §14 pass/fail slice 1: Task 5. Slice 2 LAN: Task 7.

**Gaps left intentionally:** interrupt tombstones, mic PCM, TTS, `connect`/`ghost` extra motions, GPU workers.

**Placeholder scan:** no TBD/FIXME in this plan. Headset steps cannot be fully TDD'd in CI; Python schemas and stale/uv tests are the offline gates.

**Type consistency:** `validate_instance`, `new_ulid`, `spec_uv_to_pca_viewport`, `classify_hit`, `CoordinatorState`, `handle_connection`, `DrawingStore.PlaceMark` names are stable across tasks.
