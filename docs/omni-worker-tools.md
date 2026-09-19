# Omni worker tool loop

What it is: the laptop-side Omni track for SAM2 inspect, async mesh generation,
and bounded chat tool rounds. Quest still renders; workers run on loopback HTTP;
the coordinator owns `place_generated` after a job is ready.

## Contract

- Model tools (OpenAI function shape): `inspect_objects`, `start_generation`,
  `emit_scene_ops` (max three ops per call; no `place_generated` / `place_known`
  from the model).
- Omni inputs require ≥0.5 s PCM (s16le 16 kHz mono) plus a JPEG; tool loop caps
  at four rounds (`provider/omni/reasoner.py`).
- Inspect results never include masks on the wire to the model; masks stay in
  `JobStore` for the gen worker only.
- Generated meshes: `GET http://{laptop}:{artifact_port}/artifacts/{job_id}.glb`
  with ULID `job_id` only (default port 8766 in `hello_ok.payload.artifact_port`).
- Speech after generation waits for placement ACKs; the first turn must not claim
  the GLB is already placed.
- API key only in env var `YIBU_API_KEY` (never in Unity, logs, or the repo).

## How to verify

Offline (no credit):

```bash
cd provider && . .venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Live smoke (spends credit; tiny prompt, distinct `--purpose`):

```bash
cd provider && . .venv/bin/activate && python -m omni.reasoner_smoke --purpose smoke_omni_tools --max-tokens 32
```

Unity EditMode (when the editor is available): `GeneratedMeshPlacerTests` for
artifact URL rules only (no network).

Hardware shop-to-life walks remain manual (ghost then mesh, worker-down honesty).

Design: `docs/superpowers/specs/2026-09-19-omni-worker-tools-design.md`.
Plan: `docs/superpowers/plans/2026-09-19-omni-worker-tools.md`.
