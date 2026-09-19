# SAM2 streaming: design spec

Date: 2026-09-19.

Status: draft for user review. Not an implementation plan.

Related: `docs/omni-sam2-streaming.md` (teammate's original note — superseded
by this spec where they conflict), `docs/superpowers/specs/2026-09-19-spatial-omni-assistant-design.md`
(binding product spec, §11 governs GPU residency), reviewer verdict BLOCK on PR #4
(missing files, unpinned checkpoint/runtime, underspecified memory + wire contract).

## 1. Goal

Two flows, one model.

1. **Highlight.** A frame comes in, SAM2 returns a mask for the object,
   the mask goes back as a visual highlight for the user.
2. **Cutout to 3D.** The user asks for a 3D generation of the object in
   frame: SAM2 singles it out (mask cutout) and the cutout — not the full
   frame — goes to the TripoSR worker. This replaces the rembg mask in the
   3D-gen path with a SAM2 mask.

Both flows are single-frame on demand. There is no 10fps tracking loop:
the highlight is produced per request (re-point re-masks), and the cutout
is produced once per 3D request. The video-predictor adapter built earlier
is parked in the repo but is not part of either flow.

Non-goals for this spec: multi-client sessions, Quest-LAN auth, grounder
selection, any change to slices 0–5, any edit to the binding product spec.

## 2. Architecture

One `sam2ws/` package on branch `feat/sam2-streaming`. (Named `sam2ws`, not
`sam2`: the upstream pip install owns the top-level `sam2` module and a local
`sam2/` directory would shadow it.) Upstream SAM2 stays an
installed dependency, never vendored. The naked gitlink is removed; if a
submodule is wanted later it must ship a real `.gitmodules`.

Units, each with one job:

| Unit | Job |
| --- | --- |
| `sam2ws/server.py` | Owns the image predictor, serves one segment request at a time |
| `sam2ws/protocol.py` | Versioned envelope + validation, shared by server and both clients |
| `sam2ws/harness_client.py` | Headless perf driver: disk frames in, latency/VRAM JSON out |
| `sam2ws/viewer_client.py` | Minimal webcam overlay for eyeball checks only |
| `sam2ws/session.py` | PARKED video-predictor adapter (not part of either flow) |

One client session at a time in v1. Multi-client is explicitly out.

## 3. Server core

Thin adapter over upstream `SAM2ImagePredictor`:

- Built via `build_sam2(config, checkpoint)` with a pinned SAM 2.1
  config + checkpoint triple: file, download location, SHA-256.
- Checkpoint-agnostic by flag. Default `tiny`.
- fp16 inference where it pays, single CUDA device, loopback bind only.
- One call, `segment_frame(jpeg, clicks) -> masks`: `set_image` once per
  frame, `predict` per click set with point labels (1 foreground,
  0 background). Stateless across frames — no sliding window, no mask
  carry, no memory bank to bound.
- The earlier `TrackingSession` video adapter stays in the repo, parked and
  unreferenced by either flow, in case the highlight ever needs continuous
  tracking. It is not maintained by this spec.

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

## 3b. Cutout to 3D

`cutout(jpeg, mask) -> triposr_input`: bbox crop around the mask with a
small margin, composite onto a white background at the TripoSR worker's
expected input size, and feed the existing TripoSR path unchanged
(preprocess, infer, mesh, export). The SAM2 mask replaces rembg in this
path; rembg stays as the fallback when SAM2 is unavailable.

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

1. Round-trip test: frame → click → mask over disk frames, asserting
   mask alignment to source pixels.
2. Single-frame mask latency: p50/p95 over 50 varied frames with 1..3
   objects, plus CUDA allocated/reserved peak for the segment call.
3. Cutout end-to-end: SAM2 mask → cutout → TripoSR mesh on the drone
   photo and 2–3 more objects. Record total seconds, combined peak VRAM
   (sequential loading), and silhouette IoU vs the same TripoSR run with
   a rembg mask — the cutout must not regress quality.
4. Highlight check: mask PNG decodes and overlays the source pixels
   (automated alignment assert); on-screen eyeball check stays manual on
   an attended machine.

Promotion bar: single-frame mask p95 under 1 s on tiny, and the cutout
path matches or beats rembg-mask IoU. Sequential model loading (SAM2
then TripoSR, never co-resident) is the default product path per binding
spec §11. Co-residence is out of scope until a flow needs both models
live at once.

## 7. Environment

A dedicated locked environment definition ships with the implementation:
exact Python, torch, torchvision, CUDA policy, SAM2 install method
(`SAM2_BUILD_ALLOW_ERRORS=0` with matching toolchain, or extension
intentionally disabled with degraded post-processing documented and tested),
websockets, opencv. The repo's Python 3.14 must be verified against the
chosen torch build before it becomes the default; an isolated interpreter is
acceptable for the perf work. Upstream needs Python ≥3.10 and torch ≥2.5.1.
