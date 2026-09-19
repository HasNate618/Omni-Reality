# SAM2 streaming: design spec

Date: 2026-09-19.

Status: draft for user review. Not an implementation plan.

Related: `docs/omni-sam2-streaming.md` (teammate's original note — superseded
by this spec where they conflict), `docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`
(binding product spec, §11 governs GPU residency), reviewer verdict BLOCK on PR #4
(missing files, unpinned checkpoint/runtime, underspecified memory + wire contract).

## 1. Goal

Replace the unreproducible SAM2 skeleton merge (naked gitlink, no server/client
files, no checkpoint or runtime pins) with a real, reproducible SAM2 streaming
integration, and answer one measured question: what does SAM2 tracking cost on
the demo laptop's 8GB RTX 4060 alone and next to the TripoSR worker.

Non-goals for this spec: multi-client sessions, Quest-LAN auth, grounder
selection, any change to slices 0–5, any edit to the binding product spec.

## 2. Architecture

One `sam2/` package on branch `feat/sam2-streaming`. Upstream SAM2 stays an
installed dependency, never vendored. The naked gitlink is removed; if a
submodule is wanted later it must ship a real `.gitmodules`.

Units, each with one job:

| Unit | Job |
| --- | --- |
| `sam2/server.py` | Owns the predictor, one serialized model owner, serves one client session |
| `sam2/protocol.py` | Versioned envelope + validation, shared by server and both clients |
| `sam2/harness_client.py` | Headless perf driver: replays disk frames, records latency/VRAM |
| `sam2/viewer_client.py` | Minimal webcam overlay for eyeball checks only |

One client session at a time in v1. Multi-client is explicitly out.

## 3. Server core

Thin online adapter over upstream `SAM2VideoPredictor` (approach A):

- Built via `build_sam2_video_predictor(config, checkpoint)` with a pinned
  SAM 2.1 config + checkpoint triple: file, download location, SHA-256.
- Checkpoint-agnostic by flag. Default `tiny`; `small` and `base+` run the
  same perf gate only if `tiny` leaves headroom. `large` is out on 8GB.
- fp16 inference, single CUDA device, loopback bind only.
- Online adapter: state seeded on first frame, live frames appended to
  `inference_state` with monotonic `frame_idx`. Eviction keeps the frames the
  default memory bank needs (current + 6 previous, `num_maskmem=7`) and drops
  older ones. Clicks referencing evicted `frame_id`s get an `evicted` error
  and the client re-clicks — never a silent wrong mask.
- Point clicks carry `label` (upstream requires labels; the old contract
  omitted them, which was a real bug).

## 4. Wire protocol

Versioned JSON envelope (v1):

- Request: `{v, session_id, frame_id, t_unix_ns, image:{w,h,jpeg_b64},
  clicks:[{x,y,label,obj_id}], remove:{obj_ids|all}}`.
- Reply echoes `frame_id` so async masks cannot land on newer frames.
- Masks are PNG-b64 with explicit `w,h` in model coordinates plus the scale
  factor back to source pixels (SAM2 resizes input to 1024).
- Hard caps enforced before decode: max JPEG bytes, max message size.
- Ingress queue is latest-frame-only with drop counters; 10 FPS is an ingress
  maximum, not a latency guarantee.
- Structured errors: `invalid`, `evicted`, `busy`, `oom`. No silent drops.

## 5. Error handling and safety

- Malformed or oversize input is rejected before base64 decode.
- OOM returns `oom`, resets predictor state, and stays servable. A wedged
  server is a failed test.
- Client disconnect destroys state via `reset_state` and frees CUDA
  allocations. Reconnect mints a new `session_id`; old state is gone.
- No Quest-LAN exposure until an authenticated handshake exists. Loopback for
  all perf work; LAN only behind an explicit flag plus client allowlist
  (future spec).

## 6. Testing and perf gate

No webcam required for any gate:

1. Round-trip test: frame → click → mask → remove → clear-all over disk
   frames, asserting mask alignment to source pixels.
2. Soak: multi-minute 10 FPS replay with 1..N objects plus a removal/reset
   cycle. Record mask latency p50/p95/p99, drop counters, CUDA
   allocated/reserved/peak, and idle-VRAM drift.
3. TripoSR gate, in order: TripoSR alone (reconcile the 2.49GB timing
   artifact against the 4.2GB worker-doc expectation first), SAM2-tiny alone,
   sequential handoff both directions (measure model reload cost), then one
   guarded co-resident run with a hard OOM stop.
4. Co-resident run records: `max_memory_allocated/reserved`, `nvidia-smi`
   process bytes, mask latency delta vs baseline, TripoSR latency delta,
   frame drops, fragmentation, and recovery without process restart.

Promotion bar: `tiny` leaves ≥2GB headroom with TripoSR resident and no
tail-latency blowup. Otherwise sequential handoff is the documented product
path per binding spec §11 (one resident heavy model per 8GB card). Any OOM,
growing idle VRAM, or restart-to-recover fails co-residence — it is not a
tuning invitation for the live path.

## 7. Environment

A dedicated locked environment definition ships with the implementation:
exact Python, torch, torchvision, CUDA policy, SAM2 install method
(`SAM2_BUILD_ALLOW_ERRORS=0` with matching toolchain, or extension
intentionally disabled with degraded post-processing documented and tested),
websockets, opencv. The repo's Python 3.14 must be verified against the
chosen torch build before it becomes the default; an isolated interpreter is
acceptable for the perf work. Upstream needs Python ≥3.10 and torch ≥2.5.1.
