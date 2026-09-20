# SAM2 install notes (2026-09-19, RTX 4060 laptop, NixOS)

- Upstream installed from the pinned clone (`/tmp/sam2-upstream` @ `2b90b9f`)
  via `pip install`, plus `sam2ws/requirements-lock.txt`.
- CUDA extension (`sam2._C`) did NOT build: no `nvcc`/toolchain on this box,
  install fell back to extension-disabled (upstream default
  `SAM2_BUILD_ALLOW_ERRORS=1`). Consequence per upstream INSTALL.md: small-hole
  and sprinkle post-processing is disabled. Quality impact is minor; the perf
  gate (PERF.md) measures with this exact build.
- torch 2.14.0+cu130, CUDA available. NixOS needs `LD_LIBRARY_PATH` covering
  gcc-lib, zlib, and the steam-run `libcuda.so` for any torch import — see the
  `tripo_lab/README.md` env recipe; same exports apply here.
