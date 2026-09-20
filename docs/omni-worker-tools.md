# Omni worker tool loop

What it is: the laptop-side Omni track for SAM2 inspect, async mesh generation,
and bounded chat tool rounds. Quest still renders; workers run on loopback HTTP;
the coordinator owns `place_generated` after a job is ready.

## Contract

- Model tools (OpenAI function shape): `inspect_objects`, `start_generation`,
  `emit_scene_ops` (max three ops per call; no `place_generated` / `place_known`
  from the model).
- `place_item(name, extent_m, target)` is a model tool. `extent_m` is the
  listing's own `[width, depth, height]` in metres and is **required**: the
  model may not place a listing it cannot size. The coordinator validates the
  metres, records the row, and authors the `place_generated` op, which carries
  `extent_m` when the size is known.
- A listing that is pre-baked in the coordinator registry is placed without
  queueing a worker. Generation is otherwise serialized: one worker job per
  session at a time.
- Omni inputs require ≥0.5 s PCM (s16le 16 kHz mono) plus a JPEG; tool loop caps
  at four rounds (`provider/omni/reasoner.py`).
- Inspect results never include masks on the wire to the model; masks stay in
  `JobStore` for the gen worker only.
- Generated meshes: `GET http://{laptop}:{artifact_port}/artifacts/{job_id}.glb`
  with ULID `job_id` only (default port 8766 in `hello_ok.payload.artifact_port`).
- Speech after generation waits for placement ACKs; the first turn must not claim
  the GLB is already placed.
- Live voice turn (`--planner yibu`): the turn loop binds session tools per turn
  (`YibuPlanner.bind_tools` with `state.jobs`, frame JPEG, frame_id), runs the
  bounded tool loop in `plan()`, then makes one tools-disabled closing call
  for the final line. `speak.audio` carries cloud PCM (Gemini Live leg in
  `provider/voice/cloud_speech.py`, 16 kHz mono s16le) when a synthesizer is
  attached; otherwise `audio` is `null` (degraded caption-only). Every turn
  records `voice_gate` (`passed` / `failed` / `degraded`) in session context.
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

Procedural ops (voice spec §3.1–3.2): `place_procedural` (closed grammar —
6 element kinds, 6 colors, 3 sizes, 3 materials, ≤6 elements, text on
panel/pointer only ≤40 chars) and `revise_procedural` (`drawing_id` +
`enlarge`/`shrink`/`rotate_cw`/`rotate_ccw`/`nudge`+direction/`remove`)
are valid `model_scene_op` and `scene_op` kinds with fixtures under
`protocol/fixtures/`. At most one `place_procedural` per turn (extras
drop in both `accept_model_ops`). Quest builds compositions locally in
`QuestDemo/Assets/Spatial/Procedural/ProceduralFactory.cs` (1.0 m
bounding-sphere cap, 0.03–0.40 m extents) with revise steps applied to
tracked drawings; failures ACK `invalid` and block success speech.

Design: `docs/superpowers/specs/2026-09-19-omni-worker-tools-design.md`.
Plan: `docs/superpowers/plans/2026-09-19-omni-worker-tools.md`.
