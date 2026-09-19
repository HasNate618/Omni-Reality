# SAM2 Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible SAM2 streaming server + headless perf harness + minimal viewer that replaces the PR #4 skeleton and measures tiny-first latency/VRAM alone and next to TripoSR.

**Architecture:** Thin online adapter over upstream `SAM2VideoPredictor` (public API only: `init_state`, `add_new_points_or_box`, `propagate_in_video`, `remove_object`, `reset_state`). Versioned JSON envelope over websockets, loopback-only, one session at a time, latest-frame queue.

**Tech Stack:** Python (repo venv), torch (CUDA), sam2 upstream @ `2b90b9f`, `websockets>=11.0`, `opencv-python>=4.8.0`, `unittest` (repo convention: `python -m unittest discover -s tests -v`).

## Global Constraints

- One client session at a time in v1; multi-client is out.
- Loopback bind only (`127.0.0.1:8767`); no Quest-LAN exposure until a future auth spec.
- Checkpoint default `tiny`; `small`/`base+` run the same gate only if `tiny` leaves headroom; `large` is out on 8GB.
- fp16 inference, single CUDA device.
- Eviction keeps current + 6 previous frames (`num_maskmem=7`); clicks on evicted frames get `evicted`, never a silent wrong mask.
- Point clicks always carry `label` (1 = foreground, 0 = background).
- 10 FPS is an ingress maximum with drop counters, not a latency guarantee.
- No `TBD`/`TODO`/placeholder steps; every code step shows the code.

---

### Task 1: Locked environment + upstream install + state-shape spike

**Files:**
- Create: `sam2ws/requirements-lock.txt`
- Create: `sam2ws/tests/test_state_shape.py`
- Create: `sam2ws/checkpoints/` (gitignored; add `sam2ws/checkpoints/.gitignore` with `*\n!.gitignore`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: installed `sam2` package importable as `from sam2.build_sam import build_sam2_video_predictor`; checkpoint file path convention `sam2ws/checkpoints/sam2.1_hiera_tiny.pt`.

- [ ] **Step 1: Write the lock file**

```text
torch>=2.5.1
torchvision>=0.20.1
numpy>=1.24.4
tqdm>=4.66.1
hydra-core>=1.3.2
iopath>=0.1.10
pillow>=9.4.0
websockets>=11.0
opencv-python>=4.8.0
```

Install upstream at the pinned commit inside the repo venv (no worktree pollution; read-only clone already exists at `/tmp/sam2-upstream` for reference only):

```bash
pip install /tmp/sam2-upstream
pip install -r sam2ws/requirements-lock.txt
```

Our package MUST be named `sam2ws`, never `sam2` — the upstream install owns
the top-level `sam2` module and a local `sam2/` directory would shadow it and
break upstream's own internal imports. Every new package directory ships an
empty `__init__.py` (`sam2ws/__init__.py`, `sam2ws/tests/__init__.py`).

If the CUDA extension build fails, retry once with a matching toolchain; if it still fails, set `SAM2_BUILD_ALLOW_ERRORS=0` is NOT the fallback — the fallback is extension-disabled install (default `SAM2_BUILD_ALLOW_ERRORS=1`) with degraded small-hole post-processing recorded in the Task 7 results doc. Record which path was taken in `sam2ws/INSTALL_NOTES.md` (one paragraph: extension built yes/no, torch version, CUDA version).

- [ ] **Step 2: Download the tiny checkpoint and record its hash**

```bash
mkdir -p sam2ws/checkpoints
curl -L https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt -o sam2ws/checkpoints/sam2.1_hiera_tiny.pt
sha256sum sam2ws/checkpoints/sam2.1_hiera_tiny.pt | tee sam2ws/checkpoints/SHA256SUMS
```

- [ ] **Step 3: Write the failing spike test**

```python
import unittest


class StateShapeTest(unittest.TestCase):
    def test_inference_state_keys_and_images_type(self):
        from sam2.build_sam import build_sam2_video_predictor
        predictor = build_sam2_video_predictor(
            "configs/sam2.1/sam2.1_hiera_t.yaml",
            "sam2ws/checkpoints/sam2.1_hiera_tiny.pt",
            device="cuda",
        )
        state = predictor.init_state(video_path="sam2ws/tests/data/two_frames")
        self.assertIn("images", state)
        self.assertIn("num_frames", state)
        self.assertEqual(state["num_frames"], 2)
```

Test fixture: `sam2ws/tests/data/two_frames/` containing two JPEGs (`000000.jpg`, `000001.jpg`, any 640x480 content — copy from `/home/nate/Downloads/drone.png` converted, or solid colors).

- [ ] **Step 4: Run test to verify it fails**

Run: `cd sam2ws && python -m unittest discover -s tests -v`
Expected: FAIL (config path resolution — `build_sam2_video_predictor` resolves `configs/...` relative to the installed package; fix by passing the absolute config path from the installed package location, found via `python -c "import sam2, os; print(os.path.dirname(sam2.__file__))"`).

- [ ] **Step 5: Fix config path and verify PASS**

Expected: PASS, and the test output (or a debug print kept in the spike) records `type(state["images"])` and its shape/dtype for Task 3 to rely on. Append those observed facts as a comment at the top of the test file.

- [ ] **Step 6: Commit**

```bash
git add sam2ws/requirements-lock.txt sam2ws/tests/test_state_shape.py sam2ws/tests/data/two_frames sam2ws/checkpoints/.gitignore sam2ws/INSTALL_NOTES.md
git commit -m "feat(sam2): locked env, tiny checkpoint, state-shape spike"
```

Do NOT commit the `.pt` file (gitignored).

---

### Task 2: Versioned wire protocol

**Files:**
- Create: `sam2ws/protocol.py`
- Test: `sam2ws/tests/test_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `build_request(session_id, frame_id, t_unix_ns, w, h, jpeg_bytes, clicks, remove) -> dict`; `validate_request(obj) -> dict` (raises `ProtocolError`); `build_reply(frame_id, masks, ...)` / `build_error(frame_id, code, detail)`; `encode_mask_png(mask_bool_2d) -> bytes`; constants `MAX_JPEG_BYTES = 350_000`, `PROTOCOL_VERSION = 1`, error codes `invalid/evicted/busy/oom`.

- [ ] **Step 1: Write the failing test**

```python
import unittest


class ProtocolTest(unittest.TestCase):
    def test_round_trip_and_reject_oversize(self):
        from sam2ws import protocol
        req = protocol.build_request(
            session_id="01k0000000000000000000000",
            frame_id=7,
            t_unix_ns=123,
            w=640, h=480,
            jpeg_bytes=b"\xff\xd8" + b"0" * 100,
            clicks=[{"x": 10, "y": 20, "label": 1, "obj_id": 1}],
            remove={"obj_ids": [], "all": False},
        )
        self.assertEqual(protocol.validate_request(req)["frame_id"], 7)
        bad = dict(req)
        bad["jpeg_b64"] = "!!!not-base64!!!"
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_request(bad)
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_request({"v": 999})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sam2ws && python -m unittest tests.test_protocol -v`
Expected: FAIL with "No module named 'sam2ws.protocol'" (run from repo root with `sam2ws` importable, or add `sam2ws/` to path — match however the venv resolves it and keep that for all later tasks).

- [ ] **Step 3: Write minimal implementation**

```python
import base64
import binascii
import io

import numpy as np
from PIL import Image

PROTOCOL_VERSION = 1
MAX_JPEG_BYTES = 350_000

ERROR_CODES = ("invalid", "evicted", "busy", "oom")


class ProtocolError(Exception):
    def __init__(self, code, detail=""):
        assert code in ERROR_CODES, code
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(s: str, limit: int) -> bytes:
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ProtocolError("invalid", f"bad base64: {e}")
    if len(raw) > limit:
        raise ProtocolError("invalid", f"payload {len(raw)} over limit {limit}")
    return raw


def build_request(session_id, frame_id, t_unix_ns, w, h, jpeg_bytes, clicks, remove):
    for c in clicks:
        assert set(c) == {"x", "y", "label", "obj_id"}, c
        assert c["label"] in (0, 1), c
    assert set(remove) == {"obj_ids", "all"}, remove
    return {
        "v": PROTOCOL_VERSION,
        "type": "frame",
        "session_id": session_id,
        "frame_id": frame_id,
        "t_unix_ns": t_unix_ns,
        "image": {"w": w, "h": h, "jpeg_b64": _b64e(jpeg_bytes)},
        "clicks": list(clicks),
        "remove": {"obj_ids": list(remove["obj_ids"]), "all": bool(remove["all"])},
    }


def validate_request(obj):
    if not isinstance(obj, dict) or obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("invalid", "version")
    if obj.get("type") != "frame":
        raise ProtocolError("invalid", "type")
    for key in ("session_id", "frame_id", "t_unix_ns", "image", "clicks", "remove"):
        if key not in obj:
            raise ProtocolError("invalid", f"missing {key}")
    img = obj["image"]
    raw = _b64d(img["jpeg_b64"], MAX_JPEG_BYTES)
    if len(raw) == 0:
        raise ProtocolError("invalid", "empty image")
    obj = dict(obj)
    obj["image"] = dict(img)
    obj["image"]["jpeg_raw"] = raw
    build_request(
        obj["session_id"], obj["frame_id"], obj["t_unix_ns"],
        img["w"], img["h"], b"\xff\xd8", obj["clicks"], obj["remove"],
    )
    return obj


def encode_mask_png(mask) -> bytes:
    arr = (np.asarray(mask) > 0).astype(np.uint8) * 255
    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def build_reply(frame_id, masks):
    return {
        "v": PROTOCOL_VERSION,
        "type": "result",
        "frame_id": frame_id,
        "objects": [
            {
                "obj_id": obj_id,
                "mask_png_b64": _b64e(encode_mask_png(mask)),
                "mask_w": int(np.asarray(mask).shape[1]),
                "mask_h": int(np.asarray(mask).shape[0]),
                "scale_to_source": float(scale),
            }
            for obj_id, mask, scale in masks
        ],
    }


def build_error(frame_id, code, detail=""):
    assert code in ERROR_CODES, code
    return {
        "v": PROTOCOL_VERSION,
        "type": "error",
        "frame_id": frame_id,
        "code": code,
        "detail": detail,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sam2ws && python -m unittest tests.test_protocol -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sam2ws/protocol.py sam2ws/tests/test_protocol.py
git commit -m "feat(sam2): versioned wire protocol with validation"
```

---

### Task 3: Online session adapter (windowed video state)

**Files:**
- Create: `sam2ws/session.py`
- Test: `sam2ws/tests/test_session.py`

**Interfaces:**
- Consumes: `sam2.build_sam.build_sam2_video_predictor`-compatible predictor object; `protocol.ProtocolError`.
- Produces: class `TrackingSession(predictor, work_dir, window=16, memory=7)` with methods `ingest(jpeg_bytes, w, h) -> frame_idx`, `click(frame_idx, x, y, label, obj_id)`, `remove(obj_ids, all)`, `masks_for_latest() -> list[(obj_id, mask_bool_2d, scale)]`, `reset()`. Evicted-frame clicks raise `protocol.ProtocolError("evicted", ...)`.

Design (locked): session keeps a JPEG dir with at most `window` frames. `ingest` appends; when the dir would exceed `window`, the adapter calls `predictor.init_state` on the newest `window` files and re-applies stored clicks with remapped frame indices, then continues. Between slides it calls `propagate_in_video(state, start_frame_idx=click_frame, max_frame_num_to_track=window)` and reads the latest frame output. No private upstream attributes are touched — only the five public methods.

- [ ] **Step 1: Write the failing test (fake predictor, no GPU)**

```python
import unittest


class FakePredictor:
    def __init__(self):
        self.inits = []
        self.points = []

    def init_state(self, video_path, **kw):
        self.inits.append(video_path)
        return {"fake": True, "video_path": video_path}

    def add_new_points_or_box(self, state, frame_idx, obj_id, points, labels):
        self.points.append((frame_idx, obj_id, list(points), list(labels)))
        return state, [0], [True]

    def propagate_in_video(self, state, start_frame_idx=None,
                           max_frame_num_to_track=None):
        return iter([])

    def remove_object(self, state, obj_id, strict=False, need_output=True):
        return state, [0], [True]

    def reset_state(self, state):
        return state


class SessionTest(unittest.TestCase):
    def test_slide_reinits_and_remaps_clicks(self):
        import tempfile
        from sam2ws.session import TrackingSession
        with tempfile.TemporaryDirectory() as d:
            s = TrackingSession(FakePredictor(), d, window=4, memory=7)
            for _ in range(4):
                s.ingest(b"\xff\xd8" + b"0" * 10, 8, 8)
            s.click(0, 1, 1, 1, 1)
            for _ in range(4):
                s.ingest(b"\xff\xd8" + b"0" * 10, 8, 8)
            self.assertEqual(len(s.predictor.inits), 2)
            with self.assertRaises(Exception):
                s.click(0, 1, 1, 1, 1)
```

(The `assertRaises(Exception)` covers the evicted-click path; the implementer narrows it to `ProtocolError` with code `evicted` and asserts the code explicitly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sam2ws && python -m unittest tests.test_session -v`
Expected: FAIL with "No module named 'sam2.session'".

- [ ] **Step 3: Write minimal implementation**

```python
import os

from sam2ws import protocol


class TrackingSession:
    def __init__(self, predictor, work_dir, window=16, memory=7):
        self.predictor = predictor
        self.work_dir = work_dir
        self.window = window
        self.memory = memory
        os.makedirs(work_dir, exist_ok=True)
        self._frames = []   # absolute jpeg paths, oldest first
        self._clicks = []   # (frame_idx, x, y, label, obj_id), frame_idx in current numbering
        self._base = 0      # global index of _frames[0]
        self._state = None
        self._boot()

    def _boot(self):
        seed = os.path.join(self.work_dir, "seed.jpg")
        if not self._frames:
            with open(seed, "wb") as f:
                f.write(b"\xff\xd8\xff\xd9")
            self._frames = [seed]
            self._base = 0
        self._state = self.predictor.init_state(video_path=self.work_dir)
        for (fi, x, y, lab, oid) in self._clicks:
            self.predictor.add_new_points_or_box(
                self._state, fi - self._base, oid,
                points=[[x, y]], labels=[lab],
            )

    def ingest(self, jpeg_bytes, w, h):
        idx = self._base + len(self._frames)
        path = os.path.join(self.work_dir, f"{idx:06d}.jpg")
        with open(path, "wb") as f:
            f.write(jpeg_bytes)
        if "seed.jpg" in (self._frames[0] if self._frames else ""):
            os.remove(self._frames.pop(0))
        else:
            self._frames.append(path)
            if len(self._frames) > self.window:
                drop = len(self._frames) - self.window
                for p in self._frames[:drop]:
                    if os.path.exists(p):
                        os.remove(p)
                self._frames = self._frames[drop:]
                self._base += drop
                self._clicks = [(fi, x, y, l, o) for (fi, x, y, l, o) in self._clicks
                                if fi >= self._base]
                self._boot()
                return self._base + len(self._frames) - 1
        if self._state is None:
            self._boot()
        return self._base + len(self._frames) - 1

    def _check_live(self, frame_idx):
        if frame_idx < self._base or frame_idx >= self._base + len(self._frames):
            raise protocol.ProtocolError("evicted", f"frame {frame_idx} not retained")

    def click(self, frame_idx, x, y, label, obj_id):
        self._check_live(frame_idx)
        self.predictor.add_new_points_or_box(
            self._state, frame_idx - self._base, obj_id,
            points=[[x, y]], labels=[label],
        )
        self._clicks.append((frame_idx, x, y, label, obj_id))

    def remove(self, obj_ids, all_flag):
        if all_flag:
            self.predictor.reset_state(self._state)
            self._clicks = []
            self._boot()
            return
        for oid in obj_ids:
            self.predictor.remove_object(self._state, oid)
        self._clicks = [(fi, x, y, l, o) for (fi, x, y, l, o) in self._clicks
                        if o not in set(obj_ids)]

    def masks_for_latest(self):
        latest = self._base + len(self._frames) - 1
        start = self._clicks[0][0] if self._clicks else latest
        out = []
        for frame_idx, obj_ids, mask_logits in self.predictor.propagate_in_video(
            self._state, start_frame_idx=start - self._base,
            max_frame_num_to_track=self.window,
        ):
            if frame_idx == latest - self._base:
                for oid, logits in zip(obj_ids, mask_logits):
                    mask = (logits.cpu().numpy() > 0.0).squeeze()
                    out.append((oid, mask, 1024.0 / max(1, self._last_wh[0])))
        return out

    def reset(self):
        self.predictor.reset_state(self._state)
        self._clicks = []
```

Notes the implementer must honor: `init_state` reads the whole `work_dir`, so the dir must contain ONLY the window JPEGs (the seed is removed on first real ingest); store `self._last_wh = (w, h)` in `ingest` for the scale factor; `masks_for_latest` mask tensor shape is `(1, 1, H, W)` logits — squeeze accordingly and assert 2D in code.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sam2ws && python -m unittest tests.test_session -v`
Expected: PASS (fake propagate yields no masks; that is fine — mask extraction is covered in Task 5 against the real model).

- [ ] **Step 5: Commit**

```bash
git add sam2ws/session.py sam2ws/tests/test_session.py
git commit -m "feat(sam2): windowed online tracking session"
```

---

### Task 4: WebSocket server, single session, bounded queues

**Files:**
- Create: `sam2ws/server.py`
- Test: `sam2ws/tests/test_server.py`

**Interfaces:**
- Consumes: `TrackingSession` (Task 3), `protocol` (Task 2).
- Produces: `serve(host="127.0.0.1", port=8767, session_factory=...)` blocking call; per-message behavior: validate → ingest → clicks/removes → reply or structured error; latest-frame-only queue (drop + count).

- [ ] **Step 1: Write the failing test (fake session, no GPU, real socket)**

```python
import asyncio
import json
import unittest


class FakeSession:
    def ingest(self, jpeg_bytes, w, h):
        return 0

    def click(self, *a):
        pass

    def remove(self, *a):
        pass

    def masks_for_latest(self):
        import numpy as np
        return [(1, np.zeros((4, 4), dtype=bool), 1.0)]

    def reset(self):
        pass


class ServerTest(unittest.TestCase):
    def test_frame_round_trip_and_oversize_rejected(self):
        from sam2ws import protocol, server
        import websockets
        req = protocol.build_request("s", 0, 0, 8, 8, b"\xff\xd8" + b"0" * 10,
                                     [], {"obj_ids": [], "all": False})

        async def go():
            async with server.serve_in_test(FakeSession()) as ws_port:
                import websockets as ws
                async with ws.connect(f"ws://127.0.0.1:{ws_port}") as sock:
                    await sock.send(json.dumps(req))
                    reply = json.loads(await sock.recv())
                    self.assertEqual(reply["type"], "result")
                    self.assertEqual(reply["frame_id"], 0)
                    await sock.send(json.dumps({"v": 1}))
                    err = json.loads(await sock.recv())
                    self.assertEqual(err["code"], "invalid")
        asyncio.run(go())
```

`serve_in_test` is a test-only context manager the implementer adds (binds port 0, yields the real port, shuts down on exit). It is part of the shipped `server.py`, documented as test-only.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sam2ws && python -m unittest tests.test_server -v`
Expected: FAIL with "No module named 'sam2.server'".

- [ ] **Step 3: Write minimal implementation**

Server loop requirements (no placeholders — implement all of these): `asyncio` + `websockets.serve` on `127.0.0.1:8767` (host/port parameters, loopback default, refuse non-loopback with `ValueError` unless `allow_lan=True` passed explicitly); one `TrackingSession` per connection, destroyed on disconnect (`reset()` + `del` + `torch.cuda.empty_cache()` if torch available); inbound: exactly-one-slot latest-frame buffer (a newer `frame` replaces an unprocessed one and increments `dropped`); per message: `validate_request` → `ingest` → apply `remove` then `clicks` → `masks_for_latest` → `build_reply`; any `ProtocolError` → `build_error`; `torch.cuda.OutOfMemoryError` → `session.reset()`, reply `oom`, continue serving. Model inference runs in a worker thread via `asyncio.to_thread` so the socket stays responsive. Log one line per request: `frame_id, objects, dropped, ms`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sam2ws && python -m unittest tests.test_server -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sam2ws/server.py sam2ws/tests/test_server.py
git commit -m "feat(sam2): loopback websocket server, single session"
```

---

### Task 5: Headless harness client + real-model integration test

**Files:**
- Create: `sam2ws/harness_client.py`
- Test: `sam2ws/tests/test_harness.py` (GPU integration, skipped without CUDA + checkpoint)

**Interfaces:**
- Consumes: running `server.py` on loopback; `protocol`.
- Produces: `run_harness(frames: list[jpeg_bytes], clicks, server_url) -> dict` returning `{mask_latency_ms: {p50, p95}, frames_sent, drops, cuda: {peak_alloc_gb, peak_reserved_gb}}`; CLI `python harness_client.py --frames <dir> --out timings.json`.

- [ ] **Step 1: Write the failing test**

```python
import os
import unittest


@unittest.skipUnless(os.environ.get("SAM2_GPU_TEST") == "1", "needs GPU + checkpoint")
class HarnessGpuTest(unittest.TestCase):
    def test_click_yields_nonempty_mask(self):
        import asyncio
        import threading
        import numpy as np
        from PIL import Image
        from sam2ws import server, session
        from sam2.build_sam import build_sam2_video_predictor  # noqa
```

(The test boots the real tiny predictor on CUDA, a real `TrackingSession` in a temp dir, the test server, sends 3 drone-derived frames with one foreground click at the image center, and asserts the returned mask for `frame_id=2` is non-empty and its dimensions match the source aspect. The implementer writes the full body; the assertion bar is `mask.sum() > 0` and `mask.shape == source.shape[:2]` after rescaling.)

Harness frames fixture: `sam2ws/tests/data/harness_frames/` — 5 JPEGs converted from `/home/nate/Downloads/drone.png` at 640x360.

- [ ] **Step 2: Run test to verify it is skipped, then fails without the module**

Run: `cd sam2ws && python -m unittest tests.test_harness -v`
Expected: SKIP (no env var). Then: `ls sam2ws/harness_client.py` missing — write the test file first, watch it fail on import, then implement.

- [ ] **Step 3: Write minimal implementation**

`harness_client.py`: connects, sends frames at 10 FPS max, one foreground click per configured object on the first frame, collects per-frame reply latency (send→reply wall time), reads `torch.cuda.max_memory_allocated/reset_peak_memory_stats` around the run (server side: add a `stats` message type returning `{peak_alloc_gb, peak_reserved_gb, dropped}` — implement it in this task, small addition to `server.py` with a test asserting the reply shape), writes `timings.json`, prints p50/p95.

- [ ] **Step 4: Run GPU test to verify it passes**

Run: `cd sam2ws && SAM2_GPU_TEST=1 python -m unittest tests.test_harness -v`
Expected: PASS with a non-empty mask. (Requires free GPU: stop `llama-server` first — `nvidia-smi` must show >7GB free. This ordering constraint is part of the step.)

- [ ] **Step 5: Commit**

```bash
git add sam2ws/harness_client.py sam2ws/tests/test_harness.py sam2ws/tests/data/harness_frames sam2ws/server.py sam2ws/tests/test_server.py
git commit -m "feat(sam2): headless harness + tiny GPU round-trip"
```

---

### Task 6: Minimal webcam viewer (manual only)

**Files:**
- Create: `sam2ws/viewer_client.py`

**Interfaces:**
- Consumes: running `server.py`; `protocol`.
- Produces: OpenCV window, 30 FPS local render, newest-frame send capped at 10 FPS, left-click adds object (foreground label), right-click removes nearest object id, right-click background sends remove-all, ESC quits. Overlay: decoded PNG mask alpha-blended.

No automated test (hardware-dependent). Verification is the reviewer's eye: stable FPS readout on screen, mask follows a hand for 60 seconds.

- [ ] **Step 1: Write the viewer per the interface above** (single file, <200 lines).

Key behaviors (implement all): capture thread pushes newest frame to a size-1 slot; network thread sends at most every 100ms; render thread draws last mask per object; click coordinates sent in source pixels; `evicted` replies ignored (mask overlay for that object frozen until next reply); `oom`/`error` shown as on-screen text, not a crash.

- [ ] **Step 2: Manual verify against the Task 5 server**

Run: server in one terminal, viewer in another, 60-second hand-track. Record result (pass/fail + observed FPS) in `sam2ws/VIEWER_NOTES.md`.

- [ ] **Step 3: Commit**

```bash
git add sam2ws/viewer_client.py sam2ws/VIEWER_NOTES.md
git commit -m "feat(sam2): minimal webcam viewer"
```

---

### Task 7: Perf gate runs + results doc (the actual decision)

**Files:**
- Create: `sam2ws/PERF.md` (all numbers, all conditions)
- Modify: `docs/omni-sam2-streaming.md` (rewrite to match the implementation: real paths, real protocol, real pins)

**Interfaces:**
- Consumes: harness (Task 5), TripoSR worker (`provider/gen3d`, timings method from `3d-demo/triposr_timings.json`).
- Produces: `PERF.md` with the promotion-bar verdict.

Measurement order (each a run of `harness_client.py --out`, GPU freed of `llama-server` first):

1. TripoSR alone on `/home/nate/Downloads/drone.png` (existing `tripo_run.py` method): reconcile 2.49GB artifact vs 4.2GB doc expectation; record which is right and why.
2. SAM2-tiny alone: 5-minute 10 FPS soak, 1 then 3 objects, removal/reset cycle. Record mask p50/p95/p99, drops, CUDA peak alloc/reserved, idle-VRAM drift (start vs end).
3. Sequential handoff both directions: stop session (server `reset` + `empty_cache`), run TripoSR job, restart session — record reload seconds each way.
4. Guarded co-resident (ONLY if Task 2's tiny soak leaves ≥2GB headroom over TripoSR peak): one serialized TripoSR request while tiny stays resident. Hard stop at first OOM. Record deltas vs baselines + recovery without restart.

- [ ] **Step 1: Run measurements 1–3, write PERF.md with numbers and conditions** (GPU model, driver, torch/CUDA versions, extension built or disabled, checkpoint SHA).

- [ ] **Step 2: Run measurement 4 or record why it was skipped** (headroom bar not met → skip + state that sequential handoff is the product path per binding spec §11).

- [ ] **Step 3: Rewrite `docs/omni-sam2-streaming.md`** to describe what actually exists (paths, protocol v1 summary, pins, how to verify headlessly). Keep it 30 lines max; detail lives in `sam2ws/PERF.md` and this plan's spec.

- [ ] **Step 4: Commit**

```bash
git add sam2ws/PERF.md docs/omni-sam2-streaming.md
git commit -m "docs(sam2): perf gate numbers + streaming doc rewrite"
```

---

## File map (final)

```text
sam2ws/
  requirements-lock.txt
  INSTALL_NOTES.md
  VIEWER_NOTES.md
  PERF.md
  protocol.py
  session.py
  server.py
  harness_client.py
  viewer_client.py
  checkpoints/.gitignore   (pt files never committed)
  checkpoints/SHA256SUMS
  tests/
    test_state_shape.py
    test_protocol.py
    test_session.py
    test_server.py
    test_harness.py
    data/two_frames/
    data/harness_frames/
```

Removed in Task 7's cleanup commit: the naked gitlink is replaced by this real directory (delete the gitlink entry: `git rm --cached sam2` is wrong since worktree has no sam2 dir — on this branch the tree still carries the gitlink from origin/main; remove it with `git rm sam2` before adding the directory, in Task 1's commit).

## Self-Review

**Spec coverage:** §2 units → Tasks 2–6 (protocol/session/server/harness/viewer). §3 server core → Tasks 1, 3, 4 (tiny default, windowed eviction honoring 6-frame bank, labels, fp16/device set at server build — implementer: pass `device="cuda"` and call `.half()` only on the image encoder path if upstream fp16 needs care; if fp16 breaks mask quality, fall back to fp32 and record in PERF.md). §4 protocol → Task 2 + server stats addition in Task 5. §5 errors/safety → Tasks 3 (evicted), 4 (oom reset, disconnect teardown, loopback refusal). §6 testing/gate → Tasks 5, 7. §7 environment → Task 1.

**Placeholder scan:** all code steps show code; checkpoint hash is computed in-task, not skipped; fp16 fallback is a recorded decision, not a TODO; Task 6 viewer is manual-by-design (hardware), not an untested gap — its automated coverage is the Task 5 round-trip.

**Type consistency:** `frame_idx` is a global monotonic int in session numbering, remapped to window-relative (`fi - self._base`) only at predictor calls; `scale_to_source = 1024 / source_width` recorded per mask; `remove={"obj_ids": [...], "all": bool}` shape is identical in `build_request`, `validate_request`, and tests.
