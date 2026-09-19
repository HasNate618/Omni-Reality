# SAM2 perf gate (2026-09-19, RTX 4060 laptop 8GB, NixOS)

Conditions: provider venv (torch 2.14.0+cu130), SAM2 extension DISABLED
(no nvcc; small-hole post-processing off), tiny checkpoint
`sam2.1_hiera_tiny.pt` sha256 `7402e0d8…eb34be69`, TripoSR fp32 chunk 8192,
mc resolution 256, drone photo `/home/nate/Downloads/drone.png` (3033x1705).
`llama-server` stopped during all runs (7834 MiB free at start).

## Single-frame mask (highlight flow)

`segment_frame` center click, full-res drone photo:

| metric | value |
| --- | --- |
| SAM2 load | 1.16 s |
| mask infer | 0.33 s |
| peak allocated | 0.63 GB |
| mask coverage | 0.197 (sane: drone fills ~20% of frame) |

Promotion bar was p95 < 1 s: **met** (single sample 0.33 s; 50-frame
spread not run — noted below).

## Cutout to 3D (sequential handoff)

`sam2ws/e2e_cutout_tripo.py`: SAM2 mask → `cutout_rgba` (bbox + 5% margin,
white background) → TripoSR, SAM2 freed before TripoSR loads.

| stage | time | peak VRAM |
| --- | --- | --- |
| SAM2 load + mask | 1.16 + 0.33 s | 0.63 GB |
| TripoSR load | 4.37 s | — |
| TripoSR infer + MC | 0.59 + 1.13 s | 2.49 GB |
| mesh | 22378 verts, watertight, vol 0.0145 | — |
| **combined peak (sequential)** | — | **2.49 GB** |

## Quality vs rembg baseline (silhouette IoU, 256², same harness)

| mesh | xy | xz | yz |
| --- | --- | --- | --- |
| rembg mask (baseline) | 0.217 | 0.214 | 0.205 |
| SAM2 cutout | **0.241** | **0.240** | 0.211 |

Verdict: **no regression, small win (+0.025 on two axes).** Promotion bar
(match or beat) met. The single-view fattening remains (mask 7012 px vs
silhouette ~18k px) — expected; the prior, not the mask, dominates now.

## Dropped / parked

- 10fps streaming soak: obsolete — neither flow tracks. Early soak numbers
  (p50 ~260–460 ms/mask, allocator reserved climbing to 8 GB under sustained
  video inference) are recorded here only as a warning if anyone revives
  continuous tracking: run it fp16 with a memory-fraction cap.
- Viewer Qt GUI smoke: deferred to an attended machine (see VIEWER_NOTES.md).
- 50-frame latency spread, small/base+ checkpoints: not run; tiny clears
  the bar with 3× margin so they are optional.
- TripoSR 4.2 GB worker-doc figure: measured 2.49 GB twice (mc256). The doc
  is conservative; correct it where the capacity math matters.

## Reproduce

```bash
cd provider && . .venv/bin/activate
export LD_LIBRARY_PATH=<gcc-lib>:<zlib>:<steam-run lib64>:$LD_LIBRARY_PATH
export PYTHONPATH=/tmp/triposr:/tmp/triposhim:/tmp:/home/nate/Projects/omni-3d-gen
python /home/nate/Projects/omni-3d-gen/sam2ws/e2e_cutout_tripo.py
```
