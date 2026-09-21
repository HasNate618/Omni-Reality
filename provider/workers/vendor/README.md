# Vendored worker runtime

These are third-party sources the worker services need at run time, copied into the
repo so a reboot cannot destroy them. They were previously fetched into `/tmp`
(`/tmp/triposr`, `/tmp/triposhim`, `/tmp/tripo_remap.py`), and `/tmp` does not
survive a restart — which would silently break live image-to-3D with no trace of
what went missing.

| Path | Upstream | Why it is here |
| --- | --- | --- |
| `tsr/` | [TripoSR](https://github.com/VAST-AI-Research/TripoSR) (`tsr` package) | Image-to-mesh model code. `tsr/system.py`, `tsr/utils.py` and `tsr/models/**`. |
| `torchmcubes.py` | local shim over `trimesh` | TripoSR's `tsr/models/isosurface.py` does `from torchmcubes import marching_cubes`; this provides it without a CUDA-compiled extension (there is no `nvcc` on this host). |
| `tripo_remap.py` | local helper | Remaps the published `model.ckpt` tensor names (transformers 4.x ViT naming) onto the 5.x names the vendored `tsr` expects. |

The **weights are not vendored**: `stabilityai/TripoSR`'s `config.yaml` and
`model.ckpt` (~1.7 GB) are fetched via `huggingface_hub` and cached durably under
`~/.cache/huggingface/`, so they survive a reboot without being committed here.

`tsr` has no `__init__.py` and is imported as a namespace package. That is fine as
long as no other `tsr` package is on `PYTHONPATH`; `run_workers.sh` puts this
directory first and the imports were verified to resolve here.

Do not reformat or "fix" these files to satisfy linters — they are upstream code.
Update them by re-copying from upstream, not by hand-editing.

Start the services with `../run_workers.sh`, which encodes the required environment.
