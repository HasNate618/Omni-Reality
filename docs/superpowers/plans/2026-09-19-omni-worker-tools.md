# Omni Worker Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Cloud Omni inspect a capture JPEG via SAM2, queue an image-to-3D job, plant a capture-time ghost now, then auto-place a coordinator-owned GLB and announce it with cloud speech.

**Architecture:** Python coordinator remains the only control plane. Omni calls `inspect_objects`, `start_generation`, and `emit_scene_ops` over HTTP Chat Completions with tools. Loopback workers (fake in tests, real processes later) never talk to Quest. Quest fetches `GET /artifacts/<job_id>.glb` after a coordinator-authored `place_generated`. Slice 0-2 hardcoded-mark behavior stays the default when no planner is injected so existing coordinator tests keep passing.

**Tech Stack:** Python 3 `unittest` + `jsonschema` + `httpx` + `websockets` (already in `provider/`), Unity C# `UnityWebRequest` for artifact fetch, existing `PlacementResolver` capture-time pin.

## Global Constraints

- Binding design: `docs/superpowers/specs/2026-09-19-omni-worker-tools-design.md`. If this plan and that spec disagree, stop and fix the plan.
- Voice spec still binds for keyword turn, ACK barrier, tools-disabled final line, and cloud PCM in `speak.audio`: `docs/superpowers/specs/2026-09-19-voice-spatial-omni-integration-design.md`.
- Parent spec still binds for capture-time pose, UV, stale 0.12 m, 0.25–4 m, honesty copy, and IDs: `docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`.
- Do not implement `place_procedural` / Quest keyword VAD / Gemini Live in this plan. Stub `speak.audio` with known PCM bytes in tests. Live credit smokes are Task 12 only.
- Omni never emits world coordinates, C#, URLs, file paths, `job_id`, `place_generated`, or `place_known`. Coordinator stamps `op_id` / `turn_id` / `stage_epoch` / `job_id`.
- `YIBU_API_KEY` lives only in the env var. Never in Unity, workers, logs, or the repo. Every live call has `--purpose`. Missing token counts stay `null`.
- Workers receive JPEG from the coordinator, never from Quest. Quest never calls `SAM2_WORKER_URL` or `GEN_WORKER_URL`.
- One generation job queued or running per session. Inspect timeout 8 s. Generation timeout 90 s. Artifact port default 8766. Max 4 Omni tool rounds then `ops_closed`.
- Do not import CUDA into `coordinator/server.py`. Fake workers in-process for tests; real HTTP URLs default `http://127.0.0.1:8771` (SAM2) and `http://127.0.0.1:8772` (gen).
- Canonical camera is **left**. JPEG UV is top-left `[0,1]`. Do not co-load SAM2 and the generator on one 8GB card.
- Offline Python: `cd provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`. Do not "fix" vendored yibu examples.
- Existing slice-2 tests must stay green: hello alone sends no mark; first valid frame still emits one hardcoded mark **unless** a planner is injected.
- Unity EditMode/device may be blocked by missing `libxml2.so.2`. Do not claim Quest tests passed. Attest C# by source + API names.
- Commit when the task’s tests pass. Push accepted work on `spatial-model-unity-integration`.

## File map

| Path | Role |
| --- | --- |
| `provider/protocol/schemas/inspect_objects.json` | Omni `inspect_objects` arguments |
| `provider/protocol/schemas/inspect_objects_result.json` | Tool result (boxes, no masks) |
| `provider/protocol/schemas/start_generation.json` | Omni `start_generation` arguments |
| `provider/protocol/schemas/start_generation_result.json` | Immediate `{job_id,status,frame_id}` or error |
| `provider/protocol/schemas/model_scene_op.json` | Drop `place_generated` / `place_known` from model kinds |
| `provider/protocol/schemas/scene_op.json` | Already requires `job_id` on `place_generated` |
| `provider/yibu_http.py` | `tools=` on `chat_completion`; `extract_tool_calls` |
| `provider/omni/__init__.py` | Package |
| `provider/omni/tools.py` | OpenAI tool definitions + dispatch |
| `provider/omni/reasoner.py` | Bounded tool loop, PCM+JPEG required |
| `provider/workers/__init__.py` | Package |
| `provider/workers/sam2_client.py` | `POST {SAM2_WORKER_URL}/inspect`, 8 s |
| `provider/workers/gen_client.py` | `POST /jobs`, `GET /jobs/<id>`, 90 s poll |
| `provider/coordinator/jobs.py` | `object_id` / `job_id` maps, busy, cancel |
| `provider/coordinator/artifacts.py` | `GET /artifacts/<job_id>.glb` |
| `provider/coordinator/session.py` | utterance buffers, jobs, artifact_port |
| `provider/coordinator/server.py` | `hello_ok.artifact_port`; planner hook; clear_session |
| `provider/coordinator/turn.py` | ACK barrier, completion announce |
| `provider/tests/test_omni_tools.py` | Schema + dispatch + loop |
| `provider/tests/test_workers.py` | Fake SAM2/gen HTTP |
| `provider/tests/test_jobs.py` | Mapping, busy, unknown job_id drop |
| `provider/tests/test_artifacts.py` | ULID-only path, 404 |
| `provider/tests/test_turn_tools.py` | Barrier, auto-place, announce, clear |
| `QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs` | Fetch + scale clamp + pin |
| `QuestDemo/Assets/Spatial/CoordinatorClient.cs` | Parse `artifact_port`; dispatch `place_generated` |
| `QuestDemo/Assets/Spatial/ProtocolJson.cs` | `job_id`, `artifact_port` |
| `docs/omni-spatial-loop.md` | Pointer to worker spec; no yibu in slice 2 default |

---

### Task 1: Freeze Omni tool schemas and forbid model mesh kinds

**Files:**
- Create: `provider/protocol/schemas/inspect_objects.json`
- Create: `provider/protocol/schemas/inspect_objects_result.json`
- Create: `provider/protocol/schemas/start_generation.json`
- Create: `provider/protocol/schemas/start_generation_result.json`
- Create: `provider/protocol/fixtures/valid/inspect_objects.json`
- Create: `provider/protocol/fixtures/valid/inspect_objects_result.json`
- Create: `provider/protocol/fixtures/valid/start_generation.json`
- Create: `provider/protocol/fixtures/valid/start_generation_result.json`
- Create: `provider/protocol/fixtures/invalid/inspect_foreign_fields.json`
- Modify: `provider/protocol/schemas/model_scene_op.json` (kind enum without `place_known` / `place_generated`)
- Modify: `provider/tests/test_protocol.py`

**Interfaces:**
- Consumes: `validate_instance(name: str, data: object) -> None`, `load_fixture`, existing UV `unit_pixel` pattern.
- Produces: schema names `inspect_objects`, `inspect_objects_result`, `start_generation`, `start_generation_result`. Model kinds: `mark`, `label`, `connect`, `ghost`, `remove`, `undo` only.

- [ ] **Step 1: Write the failing tests**

Append to `provider/tests/test_protocol.py`:

```python
    def test_inspect_objects_valid_and_rejects_mask(self) -> None:
        data = load_fixture("valid", "inspect_objects.json")
        validate_instance("inspect_objects", data)
        with self.assertRaises(ValidationError):
            validate_instance("inspect_objects", {**data, "mask_png_b64": "xxxx"})

    def test_inspect_result_has_boxes_not_masks(self) -> None:
        data = load_fixture("valid", "inspect_objects_result.json")
        validate_instance("inspect_objects_result", data)
        self.assertNotIn("mask", data)
        self.assertNotIn("mask_png_b64", data["objects"][0])

    def test_start_generation_valid(self) -> None:
        validate_instance("start_generation", load_fixture("valid", "start_generation.json"))
        validate_instance(
            "start_generation_result",
            load_fixture("valid", "start_generation_result.json"),
        )

    def test_model_must_not_emit_place_generated_or_place_known(self) -> None:
        target = load_fixture("valid", "scene_op_mark.json")["target"]
        for kind in ("place_generated", "place_known"):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                validate_instance("model_scene_op", {"kind": kind, "target": target})
```

In `test_model_targets_and_kind_specific_fields`, delete the `place_known` and `place_generated` entries from the `for data in (...)` loop. Keep `label`, `connect`, `remove`, `undo`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_protocol.SchemaTests.test_inspect_objects_valid_and_rejects_mask tests.test_protocol.SchemaTests.test_model_must_not_emit_place_generated_or_place_known -v`

Expected: FAIL (`FileNotFoundError` for schemas/fixtures, or `place_generated` still validates).

- [ ] **Step 3: Write schemas and fixtures**

`inspect_objects.json`: object, `additionalProperties: false`, required `frame_id` + `target`. `phrase` optional string `maxLength` 40. `target` oneOf image_point / image_box (copy `$defs` from `model_scene_op.json`). ULID pattern identical to existing.

`inspect_objects_result.json`: required `frame_id`, `count` integer minimum 0, `objects` array maxItems 3. Each object required `object_id` (ulid), `u0`,`v0`,`u1`,`v1` unit_pixel, `score` 0–1. Optional `error` enum `no_object` / `timeout` / `busy`. `additionalProperties: false` on object and items.

`start_generation.json`: `additionalProperties: false`. One of: required `object_id` (ulid), or required `target` image_box (with `frame_id`). Optional `prompt` maxLength 80. Optional top-level `frame_id` ulid.

`start_generation_result.json`: success required `job_id`, `status` const `queued`, `frame_id`. Error shape: required `error` enum `invalid` / `busy` / `unknown_object` / `missing_image`, no `job_id`. Use `oneOf` those two objects, `additionalProperties: false` each.

Fixtures: copy a real `frame_id` from `valid/capture_envelope.json`. Result object_id/job_id: any valid 26-char Crockford ulid such as `01k0workertools000000000001` only if it matches the pattern; prefer generating via a one-liner `python -c "from protocol.ids import new_ulid; print(new_ulid())"` from `provider/`.

In `model_scene_op.json` kind enum remove `place_known` and `place_generated`. Remove the `if kind place_known` allOf block. Keep `place_generated` on `scene_op.json` only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_protocol -v`

Expected: all `SchemaTests` OK, including the four new tests.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/protocol/schemas provider/protocol/fixtures provider/tests/test_protocol.py
git commit -m "$(cat <<'EOF'
Forbid model mesh ops and freeze worker tool schemas
EOF
)"
```

---

### Task 2: HTTP Chat Completions tools payload

**Files:**
- Modify: `provider/yibu_http.py`
- Modify: `provider/tests/test_examples.py`

**Interfaces:**
- Consumes: existing `chat_completion(...)`.
- Produces: `extract_tool_calls(response_json: Mapping[str, Any]) -> list[dict[str, Any]]` where each item is `{"id": str, "name": str, "arguments": dict}`. `chat_completion(..., tools: list | None = None, tool_choice: str | dict | None = None)` includes those keys in the JSON body when not None. Return type unchanged: `(text, response_json, record)`.

- [ ] **Step 1: Write the failing tests**

Add to `HttpShapeTests` in `provider/tests/test_examples.py`:

```python
from yibu_http import extract_tool_calls, chat_completion
from unittest.mock import MagicMock, patch

    def test_extract_tool_calls_parses_arguments_json(self) -> None:
        payload = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "inspect_objects",
                            "arguments": "{\"frame_id\":\"01k00000000000000000000001\"}",
                        },
                    }],
                }
            }]
        }
        calls = extract_tool_calls(payload)
        self.assertEqual(calls[0]["name"], "inspect_objects")
        self.assertEqual(calls[0]["arguments"]["frame_id"], "01k00000000000000000000001")

    def test_chat_completion_sends_tools_array(self) -> None:
        tools = [{"type": "function", "function": {"name": "inspect_objects", "parameters": {"type": "object"}}}]
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": ""}}], "usage": {}}
        response.raise_for_status = MagicMock()
        captured = {}

        def fake_post(url, headers=None, json=None):
            captured["json"] = json
            return response

        fake_client = MagicMock()
        fake_client.__enter__.return_value.post = fake_post
        fake_client.__exit__.return_value = False
        with patch("yibu_http.httpx.Client", return_value=fake_client):
            with patch("yibu_http.append_audit_record", return_value={"call_id": "x"}):
                chat_completion(
                    api_key="unit-key",
                    model="qwen3.8-omni-flash",
                    messages=[{"role": "user", "content": "hi"}],
                    purpose="unit-tools",
                    tools=tools,
                    tool_choice="auto",
                )
        self.assertEqual(captured["json"]["tools"], tools)
        self.assertEqual(captured["json"]["tool_choice"], "auto")
```

Use a frame_id that matches the ULID regex if this test also schema-validates; here it only checks JSON parse.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_examples.HttpShapeTests.test_extract_tool_calls_parses_arguments_json tests.test_examples.HttpShapeTests.test_chat_completion_sends_tools_array -v`

Expected: FAIL `ImportError` or `TypeError: unexpected keyword argument 'tools'`.

- [ ] **Step 3: Implement**

In `yibu_http.py`:

```python
def extract_tool_calls(response_json: Mapping[str, Any]) -> list[dict[str, Any]]:
    choices = response_json.get("choices") or []
    if not choices:
        return []
    message = choices[0].get("message") or {}
    raw = message.get("tool_calls") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        fn = item.get("function") or {}
        args = fn.get("arguments") or "{}"
        parsed: dict[str, Any]
        if isinstance(args, Mapping):
            parsed = dict(args)
        else:
            try:
                value = json.loads(args)
            except json.JSONDecodeError:
                value = {}
            parsed = value if isinstance(value, dict) else {}
        out.append({
            "id": str(item.get("id") or ""),
            "name": str(fn.get("name") or ""),
            "arguments": parsed,
        })
    return out
```

Add `tools: list[dict[str, Any]] | None = None` and `tool_choice: Any = None` to `chat_completion`. After building `payload`, `if tools is not None: payload["tools"] = tools` and `if tool_choice is not None: payload["tool_choice"] = tool_choice`. Do not log `messages`, `tools`, or response content in the audit record.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_examples -v`

Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/yibu_http.py provider/tests/test_examples.py
git commit -m "$(cat <<'EOF'
Pass Chat Completions tools through the HTTP helper
EOF
)"
```

---

### Task 3: SAM2 client and in-process fake worker

**Files:**
- Create: `provider/workers/__init__.py` (empty)
- Create: `provider/workers/sam2_client.py`
- Create: `provider/tests/test_workers.py`

**Interfaces:**
- Consumes: `SAM2_WORKER_URL` env default `http://127.0.0.1:8771`.
- Produces: `inspect_remote(*, base_url: str, frame_id: str, jpeg_b64: str, target: dict, phrase: str | None, timeout_s: float = 8.0) -> dict` returning the worker JSON (boxes + optional `mask_png_b64` for the coordinator only). On timeout raise `TimeoutError`. `httpx` `trust_env=False`.

- [ ] **Step 1: Write the failing tests**

Create `provider/tests/test_workers.py`:

```python
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workers.sam2_client import inspect_remote


class _Handler(BaseHTTPRequestHandler):
    response_body = b'{"objects":[]}'
    delay_s = 0.0
    last_body = None

    def do_POST(self):
        import time
        time.sleep(type(self).delay_s)
        n = int(self.headers.get("Content-Length", "0"))
        type(self).last_body = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).response_body)

    def log_message(self, format, *args):
        return


class Sam2ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        _Handler.delay_s = 0.0
        _Handler.response_body = json.dumps({
            "objects": [{"u0": 0.3, "v0": 0.2, "u1": 0.7, "v1": 0.8, "score": 0.9, "mask_png_b64": "QQ=="}],
        }).encode()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_inspect_posts_jpeg_and_target(self) -> None:
        out = inspect_remote(
            base_url=self.base,
            frame_id="01k00000000000000000000001",
            jpeg_b64="qq==",
            target={"type": "image_box", "frame_id": "01k00000000000000000000001", "u0": 0.2, "v0": 0.1, "u1": 0.8, "v1": 0.9},
            phrase="chair",
        )
        self.assertEqual(out["objects"][0]["score"], 0.9)
        posted = json.loads(_Handler.last_body.decode())
        self.assertEqual(posted["jpeg_b64"], "qq==")
        self.assertEqual(posted["phrase"], "chair")

    def test_inspect_timeout(self) -> None:
        _Handler.delay_s = 0.3
        with self.assertRaises(TimeoutError):
            inspect_remote(
                base_url=self.base,
                frame_id="01k00000000000000000000001",
                jpeg_b64="qq==",
                target={"type": "image_point", "frame_id": "01k00000000000000000000001", "u": 0.5, "v": 0.5},
                phrase=None,
                timeout_s=0.05,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_workers -v`

Expected: FAIL `ModuleNotFoundError: workers.sam2_client`.

- [ ] **Step 3: Implement `inspect_remote`**

```python
def inspect_remote(*, base_url: str, frame_id: str, jpeg_b64: str, target: dict, phrase: str | None, timeout_s: float = 8.0) -> dict:
    url = base_url.rstrip("/") + "/inspect"
    body = {"frame_id": frame_id, "jpeg_b64": jpeg_b64, "target": target}
    if phrase:
        body["phrase"] = phrase
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
            response = client.post(url, json=body)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError("sam2 inspect timed out") from exc
    if not isinstance(data, dict):
        raise RuntimeError("sam2 inspect returned non-object")
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2. Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/workers/__init__.py provider/workers/sam2_client.py provider/tests/test_workers.py
git commit -m "$(cat <<'EOF'
Add SAM2 inspect client with timeout
EOF
)"
```

---

### Task 4: Generation client, one-job busy, poll ready/failed

**Files:**
- Create: `provider/workers/gen_client.py`
- Modify: `provider/tests/test_workers.py`

**Interfaces:**
- Consumes: `GEN_WORKER_URL` default `http://127.0.0.1:8772`.
- Produces: `queue_job(*, base_url: str, job_id: str, frame_id: str, jpeg_b64: str, prompt: str | None, mask_png_b64: str | None) -> dict` expecting `{status:"queued"}`. `get_job(*, base_url: str, job_id: str, timeout_s: float = 5.0) -> dict` with `status` in `queued|running|ready|failed`. HTTP 409 → raise `BusyError`.

- [ ] **Step 1: Write the failing tests**

Add `BusyError` import expectation and:

```python
from workers.gen_client import BusyError, get_job, queue_job

class _GenHandler(BaseHTTPRequestHandler):
    queued = {}
    last_post = None
    busy = False

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        type(self).last_post = json.loads(self.rfile.read(n).decode())
        if type(self).busy:
            self.send_response(409)
            self.end_headers()
            self.wfile.write(b'{"error":"busy"}')
            return
        type(self).queued[type(self).last_post["job_id"]] = {"status": "queued"}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"queued"}')

    def do_GET(self):
        job_id = self.path.rsplit("/", 1)[-1]
        body = json.dumps(type(self).queued.get(job_id, {"status": "failed"})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
```

Tests: `test_queue_job_posts_job_id`, `test_queue_job_busy_is_409`, `test_get_job_status`. Use a second `ThreadingHTTPServer` in `setUp`/`tearDown` for gen.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_workers -v`

Expected: FAIL missing `gen_client`.

- [ ] **Step 3: Implement**

```python
class BusyError(Exception):
    pass

def queue_job(*, base_url: str, job_id: str, frame_id: str, jpeg_b64: str, prompt: str | None, mask_png_b64: str | None) -> dict:
    url = base_url.rstrip("/") + "/jobs"
    body = {"job_id": job_id, "frame_id": frame_id, "jpeg_b64": jpeg_b64}
    if prompt:
        body["prompt"] = prompt
    if mask_png_b64:
        body["mask_png_b64"] = mask_png_b64
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.post(url, json=body)
    if response.status_code == 409:
        raise BusyError("generation worker busy")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("queue_job non-object")
    return data

def get_job(*, base_url: str, job_id: str, timeout_s: float = 5.0) -> dict:
    url = base_url.rstrip("/") + "/jobs/" + job_id
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        response = client.get(url)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("get_job non-object")
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Same command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/workers/gen_client.py provider/tests/test_workers.py
git commit -m "$(cat <<'EOF'
Add generation worker client and busy handling
EOF
)"
```

---

### Task 5: Session job store — inspect dispatch, start_generation, unknown job drop

**Files:**
- Create: `provider/coordinator/jobs.py`
- Create: `provider/tests/test_jobs.py`
- Modify: `provider/coordinator/session.py`

**Interfaces:**
- Consumes: `new_ulid()`, `validate_instance`, `inspect_remote`, `queue_job`, `BusyError`, `TimeoutError`.
- Produces:
  - `class JobStore` on session: `objects: dict[str, dict]`, `jobs: dict[str, dict]`.
  - `async def handle_inspect(store, *, frame_id, jpeg_b64, target, phrase, current_frame_id, inspect_fn) -> dict` validated `inspect_objects_result`.
  - `async def handle_start_generation(store, *, args, current_frame_id, jpeg_b64, queue_fn) -> dict` validated `start_generation_result`.
  - `def coordinator_may_place(store, job_id: str) -> bool` True only if this session issued `job_id` and status is `ready`.
  - `def mark_ready(store, job_id: str) -> None` sets `store.jobs[job_id]["status"] = "ready"` if present.
  - `def mark_failed(store, job_id: str) -> None` sets status `"failed"` if present.
  - `def clear_jobs(store) -> None` deletes maps (media paths later Task 10).
  - `inspect_fn` and `queue_fn` are sync callables returning dicts (or raising `TimeoutError` / `BusyError`). `handle_inspect` / `handle_start_generation` stay `async` for the coordinator.

`inspect_fn` / `queue_fn` are injected so tests never open sockets.

- [ ] **Step 1: Write the failing tests**

Create `provider/tests/test_jobs.py`:

```python
from __future__ import annotations

import asyncio
import unittest

from coordinator.jobs import JobStore, coordinator_may_place, handle_inspect, handle_start_generation
from protocol.ids import new_ulid
from protocol.validate import load_fixture, validate_instance


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = JobStore()
        self.frame_id = load_fixture("valid", "capture_envelope.json")["frame_id"]
        self.target = {
            "type": "image_box",
            "frame_id": self.frame_id,
            "u0": 0.2, "v0": 0.1, "u1": 0.8, "v1": 0.9,
        }
        self.jpeg = "qq=="

    def test_inspect_stamps_object_id_and_strips_mask(self) -> None:
        async def inspect_fn(**kwargs):
            return {"objects": [{
                "u0": 0.31, "v0": 0.22, "u1": 0.74, "v1": 0.81,
                "score": 0.91, "mask_png_b64": "MASK",
            }]}

        result = asyncio.run(handle_inspect(
            self.store,
            frame_id=self.frame_id,
            jpeg_b64=self.jpeg,
            target=self.target,
            phrase="chair",
            current_frame_id=self.frame_id,
            inspect_fn=inspect_fn,
        ))
        validate_instance("inspect_objects_result", result)
        self.assertEqual(result["count"], 1)
        oid = result["objects"][0]["object_id"]
        self.assertNotIn("mask_png_b64", result["objects"][0])
        self.assertEqual(self.store.objects[oid]["mask_png_b64"], "MASK")

    def test_inspect_foreign_frame_is_error_not_boxes(self) -> None:
        result = asyncio.run(handle_inspect(
            self.store,
            frame_id=new_ulid(),
            jpeg_b64=self.jpeg,
            target=self.target,
            phrase=None,
            current_frame_id=self.frame_id,
            inspect_fn=lambda **k: (_ for _ in ()).throw(AssertionError("must not call worker")),
        ))
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["objects"], [])
        self.assertEqual(result["error"], "invalid")

    def test_inspect_timeout_error(self) -> None:
        async def inspect_fn(**kwargs):
            raise TimeoutError("sam2")

        result = asyncio.run(handle_inspect(
            self.store, frame_id=self.frame_id, jpeg_b64=self.jpeg,
            target=self.target, phrase=None, current_frame_id=self.frame_id,
            inspect_fn=inspect_fn,
        ))
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["count"], 0)

    def test_start_generation_queues_and_busy(self) -> None:
        asyncio.run(handle_inspect(
            self.store, frame_id=self.frame_id, jpeg_b64=self.jpeg,
            target=self.target, phrase=None, current_frame_id=self.frame_id,
            inspect_fn=lambda **k: {"objects": [{"u0": 0.3, "v0": 0.2, "u1": 0.7, "v1": 0.8, "score": 0.9}]},
        ))
        oid = next(iter(self.store.objects))
        queued = []

        async def queue_fn(**kwargs):
            queued.append(kwargs)
            return {"status": "queued"}

        result = asyncio.run(handle_start_generation(
            self.store, args={"object_id": oid, "prompt": "chair"},
            current_frame_id=self.frame_id, jpeg_b64=self.jpeg, queue_fn=queue_fn,
        ))
        validate_instance("start_generation_result", result)
        self.assertEqual(result["status"], "queued")
        busy = asyncio.run(handle_start_generation(
            self.store, args={"object_id": oid},
            current_frame_id=self.frame_id, jpeg_b64=self.jpeg, queue_fn=queue_fn,
        ))
        self.assertEqual(busy["error"], "busy")
        self.assertEqual(len(queued), 1)

    def test_unknown_job_may_not_place(self) -> None:
        self.assertFalse(coordinator_may_place(self.store, new_ulid()))
```

For foreign-frame error, either add `invalid` to the inspect result `error` enum in Task 1 schema or map foreign frame to `no_object`. Prefer adding `invalid` to `inspect_objects_result.error` enum in this task if Step 1 of Task 1 omitted it — update the schema here in the same commit.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_jobs -v`

Expected: FAIL missing `coordinator.jobs`.

- [ ] **Step 3: Implement `JobStore` and handlers**

Rules:
- Foreign `frame_id` vs `current_frame_id` → do not call `inspect_fn`; result `{frame_id, count:0, objects:[], error:"invalid"}`.
- `TimeoutError` → `error:"timeout"`. Empty worker objects → `error:"no_object"`.
- Cap objects at 3, sort by score descending, stamp each `object_id=new_ulid()`, keep mask only in `store.objects[id]`.
- `handle_start_generation`: if any job has status `queued` or `running`, return `{error:"busy"}`. Unknown `object_id` → `{error:"unknown_object"}`. Missing jpeg → `{error:"missing_image"}`. Else `job_id=new_ulid()`, record `{job_id, frame_id, target, object_id, status:"queued"}`, call `queue_fn`, return `{job_id, status:"queued", frame_id}`.
- `coordinator_may_place`: job exists, `status=="ready"`.
- Add `self.jobs = JobStore()` on `CoordinatorState.__init__`.

- [ ] **Step 4: Run tests to verify they pass**

Same command plus `python -m unittest tests.test_coordinator tests.test_priority -v` to confirm session change did not break slice 2.

Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/coordinator/jobs.py provider/coordinator/session.py provider/tests/test_jobs.py provider/protocol/schemas/inspect_objects_result.json
git commit -m "$(cat <<'EOF'
Stamp inspect object ids and gate generation jobs
EOF
)"
```

---

### Task 6: Omni tool loop (PCM+JPEG required, max 4 rounds)

**Files:**
- Create: `provider/omni/__init__.py`
- Create: `provider/omni/tools.py`
- Create: `provider/omni/reasoner.py`
- Create: `provider/tests/test_omni_tools.py`

**Interfaces:**
- Consumes: `extract_tool_calls`, `validate_instance`, `handle_inspect`, `handle_start_generation`.
- Produces:
  - `TOOL_DEFINITIONS: list[dict]` OpenAI function tools for `inspect_objects`, `start_generation`, `emit_scene_ops` (parameters wrap the JSON schemas’ properties).
  - `class ReasonerTurn`: `pcm_bytes: bytes`, `jpeg_b64: str | None`, `frame_id: str | None`.
  - `def require_omni_inputs(pcm_bytes: bytes, jpeg_b64: str | None) -> str | None` returns error string if PCM < 0.5 s of s16le 16 kHz mono (`len(pcm) < 16000`) or jpeg missing; else None.
  - `async def run_tool_loop(*, complete_fn, execute_fn, messages, max_rounds: int = 4) -> tuple[list[dict], str]` where `complete_fn(messages, tools_enabled: bool)` returns a fake OpenAI `response_json`. Stops after 4 tool rounds even if the model keeps calling. Collects `emit_scene_ops` argument lists (not executed against Quest here).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import asyncio
import json
import unittest

from omni.reasoner import require_omni_inputs, run_tool_loop
from protocol.validate import load_fixture


class ReasonerTests(unittest.TestCase):
    def test_transcript_only_fails_gate(self) -> None:
        self.assertIsNotNone(require_omni_inputs(b"\x00\x00", None))
        pcm = b"\x00\x00" * 8000  # 8000 s16le samples = 0.5 s at 16 kHz
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
```

This task does not call `complete_fn` with `tools_enabled=False`. That tools-disabled final line is Task 7/9 after ACKs.
- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_omni_tools -v`

Expected: FAIL missing `omni.reasoner`.

- [ ] **Step 3: Implement loop**

`require_omni_inputs`: 16 kHz mono s16le 0.5 s = 16000 bytes. JPEG string non-empty.

`run_tool_loop`: for `i in range(max_rounds)` call `complete_fn(..., True)`, parse tool_calls via `extract_tool_calls`. If none, break. For each call, if name is `emit_scene_ops`, extend `ops` from `arguments["ops"]` (list, cap 3 total). Else `tool_result = await execute_fn(name, arguments)` and append a `role: tool` message `{tool_call_id, content: json.dumps(tool_result)}` to `messages`. Never send masks in tool results.

`TOOL_DEFINITIONS` three functions; `emit_scene_ops` parameters `{type:object, properties:{ops:{type:array,maxItems:3}}, required:["ops"]}`.

- [ ] **Step 4: Run tests to verify they pass**

Same command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/omni provider/tests/test_omni_tools.py
git commit -m "$(cat <<'EOF'
Add bounded Omni tool loop with PCM and JPEG gate
EOF
)"
```

---

### Task 7: Validate emit_scene_ops, ACK barrier, no speak before terminal ACKs

**Files:**
- Create: `provider/coordinator/turn.py`
- Create: `provider/tests/test_turn_tools.py`
- Modify: `provider/omni/tools.py` (`accept_model_ops`)

**Interfaces:**
- Consumes: `validate_instance("model_scene_op")`, existing DummyWs patterns from `tests/test_coordinator.py`.
- Produces:
  - `def accept_model_ops(raw_ops: list, *, max_ops: int = 3) -> list[dict]` drops invalid, drops `place_generated`/`place_known` even if they sneak past schema, caps at 3.
  - `async def freeze_and_ack(*, send_ops, wait_acks, timeout_s: float = 1.5) -> list[dict]` sends frozen `scene_op`s then waits. `speak` is not this function’s job.
  - `def may_speak(acks: list[dict] | None, timed_out: bool) -> bool` False if timeout, any `rejected`/`stale`, or missing ack. True if zero ops (empty list, not None) or all `placed`/`applied`.

Slice-2 hardcoded mark path is unchanged. These helpers are unused by `handle_connection` until Task 8/9 injects a planner.

- [ ] **Step 1: Write the failing tests**

```python
from coordinator.turn import accept_model_ops, may_speak
from protocol.validate import load_fixture

class AcceptOpsTests(unittest.TestCase):
    def test_drops_place_generated_and_caps(self) -> None:
        target = load_fixture("valid", "scene_op_mark.json")["target"]
        ghost = {"kind": "ghost", "target": target, "motion": {"kind": "rotate", "axis": "y", "angle_deg": 30, "period_s": 2}}
        ops = accept_model_ops([
            {"kind": "place_generated", "target": target, "job_id": load_fixture("valid", "scene_op_mark.json")["op_id"]},
            ghost, ghost, ghost, ghost,
        ])
        self.assertEqual(len(ops), 3)
        self.assertTrue(all(op["kind"] == "ghost" for op in ops))

class SpeakGateTests(unittest.TestCase):
    def test_no_speak_on_reject_stale_or_timeout(self) -> None:
        self.assertFalse(may_speak([{"status": "rejected"}], False))
        self.assertFalse(may_speak([{"status": "stale"}], False))
        self.assertFalse(may_speak(None, True))
        self.assertTrue(may_speak([], False))
        self.assertTrue(may_speak([{"status": "placed"}], False))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_turn_tools -v`

Expected: FAIL missing `coordinator.turn`.

- [ ] **Step 3: Implement `accept_model_ops` and `may_speak`**

Try `validate_instance("model_scene_op", op)` per item; skip failures; skip kinds `place_generated` and `place_known`; `return accepted[:3]`.

`may_speak`: if `timed_out`: False. if `acks is None`: False. if not acks: True. if any status not in `placed`,`applied`: False. else True.

- [ ] **Step 4: Run tests to verify they pass**

Same command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/coordinator/turn.py provider/omni/tools.py provider/tests/test_turn_tools.py
git commit -m "$(cat <<'EOF'
Reject model mesh kinds and gate speak on ACKs
EOF
)"
```

---

### Task 8: Artifact HTTP server (ULID job_id only)

**Files:**
- Create: `provider/coordinator/artifacts.py`
- Create: `provider/tests/test_artifacts.py`

**Interfaces:**
- Consumes: `JobStore`, filesystem dir default `provider/artifacts/generated/` (already gitignored via `provider/artifacts/`).
- Produces: `def artifact_path(root: Path, job_id: str) -> Path | None` None unless `job_id` matches the protocol ULID regex. `class ArtifactHandler` serves `GET /artifacts/<job_id>.glb` as `model/gltf-binary` from `root / f"{job_id}.glb"` when `coordinator_may_place` is true OR when the file exists and job_id is a valid ULID **and** is in `store.jobs` (even `ready` only). 404 otherwise. No directory listing. Refuse `..` and non-`.glb`.

Default port `ARTIFACT_PORT=8766`.

- [ ] **Step 1: Write the failing tests**

```python
from coordinator.artifacts import artifact_path, serve_artifact_bytes
from protocol.ids import new_ulid

class ArtifactTests(unittest.TestCase):
    def test_rejects_non_ulid_and_dotdot(self) -> None:
        root = Path(self.tmpdir)
        self.assertIsNone(artifact_path(root, "../etc/passwd"))
        self.assertIsNone(artifact_path(root, "not-a-ulid"))
        job_id = new_ulid()
        self.assertEqual(artifact_path(root, job_id), root / f"{job_id}.glb")

    def test_unknown_job_is_404(self) -> None:
        store = JobStore()
        job_id = new_ulid()
        self.assertIsNone(serve_artifact_bytes(store, Path(self.tmpdir), job_id))
```

Use `tempfile.TemporaryDirectory` in `setUp`. After a job is `ready` and file written with 12-byte `b"glTF" + b"\x00"*8`, `serve_artifact_bytes` returns those bytes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_artifacts -v`

Expected: FAIL missing module.

- [ ] **Step 3: Implement**

ULID regex: copy from schema `^[0-7][0-9a-hjkmnp-tv-z]{25}$`. `serve_artifact_bytes`: if not `coordinator_may_place` and status is not `ready`, return None. If file missing or size > 25 * 1024 * 1024, return None. If file does not start with `b"glTF"`, return None.

Also `async def start_artifact_server(host, port, store, root)` using `ThreadingHTTPServer` in a daemon thread for production; tests call `serve_artifact_bytes` only (no bind required).

- [ ] **Step 4: Run tests to verify they pass**

Same command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/coordinator/artifacts.py provider/tests/test_artifacts.py
git commit -m "$(cat <<'EOF'
Serve generated GLBs only by session job id
EOF
)"
```

---

### Task 9: Auto-place on ready, tools-disabled announce, hello_ok.artifact_port

**Files:**
- Modify: `provider/coordinator/server.py` (`hello_ok` payload; skip hardcoded mark when `state.planner` is set)
- Modify: `provider/coordinator/session.py` (`planner: object | None = None`, `artifact_port: int = 8766`)
- Modify: `provider/coordinator/jobs.py` (`mark_ready`, `mark_failed`)
- Modify: `provider/coordinator/turn.py` (`build_place_generated`, `announce_after_place`)
- Modify: `provider/tests/test_turn_tools.py`
- Modify: `provider/tests/test_coordinator.py` (`test_hello_ok_assigns_session` asserts `artifact_port == 8766`)

**Interfaces:**
- Consumes: `coordinator_may_place`, `accept_model_ops`, `may_speak`, `validate_instance("scene_op")`.
- Produces:
  - `hello_ok.payload.artifact_port` int default 8766.
  - `def build_place_generated(*, job_id, turn_id, stage_epoch, target) -> dict` coordinator-authored SceneOp.
  - `async def on_job_terminal(state, job_id, send, complete_final_fn)` if ready and file valid: send `place_generated`, wait ACK 1.5 s, then `complete_final_fn(acks)` with tools disabled; if failed/timeout/stale: `complete_final_fn` with honest failure context, no mesh op. Drop if `stage_epoch` < `state.latest_stage_epoch` or store cleared (`superseded`). Never retry same `op_id`.
  - First-turn speech must not include a claim that the GLB is placed. Tests check announce text is whatever `complete_final_fn` returns; the stub final fn receives ACK JSON.

- [ ] **Step 1: Write the failing tests**

In `provider/tests/test_coordinator.py` `test_hello_ok_assigns_session`, after reading `hello_ok` payload add:

```python
self.assertEqual(hello_ok["payload"]["artifact_port"], 8766)
```

In `provider/tests/test_turn_tools.py`:

```python
from coordinator.jobs import JobStore, coordinator_may_place, mark_failed, mark_ready
from coordinator.session import CoordinatorState
from coordinator.turn import build_place_generated, on_job_terminal
from protocol.ids import new_ulid
from protocol.validate import load_fixture, validate_instance


class PlaceGeneratedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = load_fixture("valid", "scene_op_mark.json")["target"]
        self.store = JobStore()
        self.state = CoordinatorState()
        self.state.jobs = self.store
        self.state.latest_stage_epoch = 1
        self.sent = []
        self.announced = []

    def test_unknown_job_place_generated_schema_valid_but_may_not_place(self) -> None:
        op = build_place_generated(
            job_id=new_ulid(), turn_id=2, stage_epoch=1, target=self.target,
        )
        validate_instance("scene_op", op)
        self.assertFalse(coordinator_may_place(self.store, op["job_id"]))

    def test_ready_job_emits_place_generated_then_announce(self) -> None:
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
        self.assertEqual(self.sent[0]["kind"], "place_generated")
        self.assertEqual(self.sent[0]["job_id"], job_id)
        self.assertEqual(self.announced[0]["status"], "placed")

    def test_failed_job_does_not_emit_place_generated(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "queued",
            "stage_epoch": 1,
        }
        mark_failed(self.store, job_id)

        async def send(op):
            self.sent.append(op)
            return {"status": "placed"}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.announced[0]["status"], "failed")

    def test_stale_epoch_is_superseded(self) -> None:
        job_id = new_ulid()
        self.store.jobs[job_id] = {
            "job_id": job_id,
            "frame_id": self.target["frame_id"],
            "target": self.target,
            "status": "ready",
            "stage_epoch": 1,
        }
        self.state.latest_stage_epoch = 2

        async def send(op):
            self.sent.append(op)
            return {"status": "placed"}

        async def complete_final_fn(ack):
            self.announced.append(ack)

        asyncio.run(on_job_terminal(self.state, job_id, send, complete_final_fn))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.announced, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_coordinator.CoordinatorTests.test_hello_ok_assigns_session tests.test_turn_tools -v`

Expected: FAIL missing `artifact_port` and missing `build_place_generated`.

- [ ] **Step 3: Implement**

`_handle_hello` payload:

```python
{"session_id": session_id, "laptop_t_unix_ns": _laptop_now_ns(), "artifact_port": state.artifact_port}
```

`build_place_generated`:

```python
def build_place_generated(*, job_id: str, turn_id: int, stage_epoch: int, target: dict) -> dict:
    op = {
        "op_id": new_ulid(),
        "turn_id": turn_id,
        "stage_epoch": stage_epoch,
        "kind": "place_generated",
        "drawing_id": None,
        "job_id": job_id,
        "target": target,
    }
    validate_instance("scene_op", op)
    return op
```

`on_job_terminal`: if job missing or status not `ready`/`failed`: return. If `failed`: call `complete_final_fn({"status": "failed"})` without sending ops. If `ready` and `job["stage_epoch"] < state.latest_stage_epoch`: return superseded. Else send op, wait ack (injectable), then `complete_final_fn(ack)`.

Hardcoded `_handle_frame` mark: wrap existing `_send_mark` in `if state.planner is None:`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`

Expected: OK, including original 61 plus new tests. Hello-then-frame still emits one mark when planner is None.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/coordinator provider/tests/test_turn_tools.py provider/tests/test_coordinator.py
git commit -m "$(cat <<'EOF'
Auto-place generated meshes after job ready
EOF
)"
```

---

### Task 10: Clear jobs on clear_session, cancel, and drop

**Files:**
- Modify: `provider/coordinator/server.py`
- Modify: `provider/coordinator/jobs.py` (`clear_jobs`)
- Modify: `provider/tests/test_jobs.py`

**Interfaces:**
- Consumes: existing `handle_text` dispatch.
- Produces: on `clear_session`, cancel of originating utterance, or `handle_connection` finally: `clear_jobs(state.jobs)` and delete `*.glb` / mask files under the artifact root for those job_ids. Late `ready` after clear does not place (`coordinator_may_place` False).

- [ ] **Step 1: Write the failing tests**

```python
    def test_clear_jobs_blocks_place(self) -> None:
        # after handle_start_generation, mark_ready, clear_jobs → may_place False

    def test_clear_session_message_clears_store(self) -> None:
        # DummyWs + handle_connection: hello, then clear_session, jobs empty
```

`clear_session` wrapper:

```python
{
  "v": 1, "type": "clear_session", "session_id": session_id,
  "turn_id": 0, "utterance_id": None, "payload": {"reason": "user"},
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_jobs.JobStoreTests.test_clear_jobs_blocks_place -v`

Expected: FAIL until `clear_jobs` exists and server handles `clear_session`.

- [ ] **Step 3: Implement**

`clear_jobs`: empty `objects` and `jobs`; unlink artifact files best-effort (`Path.unlink(missing_ok=True)`).

`handle_text`: if type `clear_session` and session allowed: `clear_jobs`, send `session_cleared` `{session_id, generation: state.clear_generation}` incrementing an int on `CoordinatorState` starting at 1.

`handle_connection` `finally: clear_jobs(state.jobs)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: full `python -m unittest discover -s tests -v`. Expected: OK.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add provider/coordinator/server.py provider/coordinator/jobs.py provider/coordinator/session.py provider/tests/test_jobs.py
git commit -m "$(cat <<'EOF'
Clear generation jobs on session end
EOF
)"
```

---

### Task 11: Quest fetch-and-pin generated mesh

**Files:**
- Create: `QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs`
- Create: `QuestDemo/Assets/Spatial/Generated/GeneratedMeshPlacer.cs.meta` (Unity GUID; if editor cannot run, create `.meta` with a new unique guid)
- Modify: `QuestDemo/Assets/Spatial/ProtocolJson.cs` (`TryGetIntField` for `artifact_port`; `job_id` on `SceneOpMsg`)
- Modify: `QuestDemo/Assets/Spatial/CoordinatorClient.cs`

**Interfaces:**
- Consumes: existing `PlacementResolver.TryPlaceFromCapture`, `CaptureGeometryCache`, laptop IPv4 player pref already used for WebSocket.
- Produces: `GeneratedMeshPlacer.TryHandle(SceneOpMsg op, string laptopIpv4, int artifactPort, CaptureGeometryCache cache)` — if kind is not `place_generated`, return false. If `job_id` missing, ACK `rejected`/`invalid`. GET `http://{ipv4}:{port}/artifacts/{job_id}.glb`. File missing, not starting with `glTF`, or > 25 MB → ACK `rejected`/`invalid`. On pin failure use existing honesty outcomes. On success, uniform-scale AABB to fit inside 1.0 m sphere (compute renderer bounds; if already smaller, leave scale). ACK `placed` with new `drawing_id` ULID.

Unity tests may not run on this host. Add an EditMode test file `QuestDemo/Assets/Tests/Editor/GeneratedMeshPlacerTests.cs` that tests URL builder and ULID path rejection with no network:

```csharp
public void ArtifactUrl_UsesJobIdOnly()
{
    string url = GeneratedMeshPlacer.ArtifactUrl("10.0.0.8", 8766, "01k00000000000000000000001");
    Assert.AreEqual("http://10.0.0.8:8766/artifacts/01k00000000000000000000001.glb", url);
}

public void ArtifactUrl_RejectsDotDot()
{
    Assert.IsNull(GeneratedMeshPlacer.ArtifactUrl("10.0.0.8", 8766, "../x"));
}
```

`CoordinatorClient.HandleMessage` `hello_ok`: parse `artifact_port` default 8766 into a field. `scene_op`: if `op.Kind == "place_generated"` call placer instead of `HandleMark`.

Do not HTTP POST to yibuapi. Do not store `YIBU_API_KEY`.

- [ ] **Step 1: Write the failing EditMode tests** (file above). If Unity cannot compile, still add the C# and note unrun.

- [ ] **Step 2: Run EditMode if possible**

Run: Unity batch EditMode for `GeneratedMeshPlacerTests`. Expected: FAIL until placer exists. If `libxml2.so.2` missing, skip run and continue; do not claim pass.

- [ ] **Step 3: Implement placer + protocol fields**

`ArtifactUrl`: if job_id length != 26 or contains `/` or `.` except none — job_id is Crockford, no dots — return null on `/`, `\`, `.`.

Fetch with `UnityWebRequest.Get` on a background wait, apply on main thread via existing pump if `CoordinatorClient` already pumps inbound on main thread (it does). Load GLB via UnityGLTF if present in packages; if the project has no glTF importer, instantiate a 0.1 m cube placeholder **only when** `UNITY_EDITOR && OMNI_FAKE_GLB` — production Quest build must use a real importer. Check `QuestDemo/Packages/manifest.json` for `com.unity.cloud.gltfast` or UnityGLTF. If neither exists, add `com.unity.cloud.gltfast` to `manifest.json` in this task (no API key).

Scale: `var size = bounds.size; float m = Mathf.Max(size.x, size.y, size.z); if (m > 1f) t.localScale *= 1f / m;`

- [ ] **Step 4: Source-attest**

Grep `GeneratedMeshPlacer.cs` for `YIBU`, `yibu`, `apiKey` — no matches. Grep `place_generated` dispatch in `CoordinatorClient.cs`.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add QuestDemo/Assets/Spatial QuestDemo/Assets/Tests QuestDemo/Packages/manifest.json
git commit -m "$(cat <<'EOF'
Fetch generated GLBs onto capture-time pins
EOF
)"
```

---

### Task 12: Docs, smoke stubs, and residual hardware gates

**Files:**
- Modify: `docs/omni-spatial-loop.md`
- Modify: `AGENTS.md` (one Layout line only if a new topic doc is added; prefer a short pointer inside `omni-spatial-loop.md` rather than growing AGENTS)
- Create: `provider/omni/reasoner_smoke.py` (live, not run in unittest)
- Create: `docs/omni-worker-tools.md` (what it is, contract, how to verify)

**Interfaces:**
- Consumes: binding spec §9.
- Produces: topic doc shape required by `AGENTS.md` Create rule.

- [ ] **Step 1: Write `docs/omni-worker-tools.md`**

What it is: Omni-track worker loop (SAM2 inspect, async generation, auto-place). Contract: tools `inspect_objects` / `start_generation` / `emit_scene_ops`; Quest artifact GET; no model URLs; ACK before success speech; key only `YIBU_API_KEY`. How to verify: offline unittest command; live smoke `python -m omni.reasoner_smoke --purpose smoke_omni_tools` (tiny PCM+JPEG, `--max-tokens 32`); hardware shop-to-life unrun.

Add one AGENTS Layout bullet: `` `docs/omni-worker-tools.md`: Omni-track SAM2/generation tool loop. ``

Update `docs/omni-spatial-loop.md`: slice 2 default still no yibu; worker loop is the other doc.

- [ ] **Step 2: `reasoner_smoke.py`**

Must refuse to run without `YIBU_API_KEY`. Builds messages with `build_omni_messages` plus `tools=TOOL_DEFINITIONS`. Does not print the key. Does not write JPEG/PCM into the repo. Purpose default `smoke_omni_tools`. This file is not imported by unittest.

- [ ] **Step 3: Offline suite**

Run: `cd /home/nate/Projects/omni-slices-0-2/provider && . /home/nate/Projects/omni/provider/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`

Expected: OK. Do not run the live smoke unless the user explicitly spends credit.

- [ ] **Step 4: Secret scan**

Run: `rg -n -i 'sk-|YIBU_API_KEY=' docs/omni-worker-tools.md provider/omni provider/workers provider/coordinator || true`

Expected: no key literals. `YIBU_API_KEY` as env-var name is allowed.

- [ ] **Step 5: Commit**

```bash
cd /home/nate/Projects/omni-slices-0-2
git add docs/omni-worker-tools.md docs/omni-spatial-loop.md AGENTS.md provider/omni/reasoner_smoke.py
git commit -m "$(cat <<'EOF'
Document Omni worker tool loop verification
EOF
)"
```

Hardware still unrun: shop-to-life ghost then mesh walk-around; SAM2-down honesty; gen-timeout honesty; cloud PCM on announce turn.

---

## Self-review (spec coverage)

| Spec section | Task |
| --- | --- |
| §2 topology, loopback, no Quest→worker | 3, 4, 8, 11 |
| §3.1 inspect_objects + no masks to Omni | 1, 3, 5 |
| §3.2 start_generation immediate job_id, busy | 1, 4, 5 |
| §3.3 emit_scene_ops, no model place_generated | 1, 6, 7 |
| §3.4 ACK then speech, no GLB claim yet | 7, 9 |
| §4 auto-place, artifact GET, announce, scale cap | 8, 9, 11 |
| §5 worker HTTP, 8 s / 90 s, one job | 3, 4, 5 |
| §6 GPU serialize / overflow URLs | 3, 4 (env URLs); real unload is ops, not code in coordinator |
| §7 errors + privacy clear | 5, 9, 10 |
| §8 schemas, artifact_port, chat tools | 1, 2, 9 |
| §9 offline fakes, smokes, hardware unrun | 3–10, 12 |

Not in this plan (separate lanes): Quest keyword/`QuestDemo/Assets/Voice/`, `place_procedural` factory, real SAM2/TripoSR CUDA processes, Gemini Live PCM. GPU unload-between-models is an operator script on the worker hosts, not the WebSocket process.
