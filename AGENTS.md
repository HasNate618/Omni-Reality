# AGENTS.md — Omni-Reality

Quest 3S spatial assistant for the Huawei OMNI Live track. Unity renders on-device; Python owns model I/O.

## Layout

- `QuestDemo/` — Unity native app (camera, depth, raycast, overlays stay on-device).
- `provider/` — yibuapi gateway client (Python). Setup/usage docs in `provider/README.md`.
- `docs/` — one doc per topic (see Documentation procedure). Start here: `docs/omni-provider-api.md` (gateway contract), `docs/questdemo-build.md` (Unity→Quest build), `docs/omni-live-pitch.md` (product), `docs/omni-live-research.md` (research). Design specs live in `docs/superpowers/specs/`.

## Documentation procedure

AGENTS.md is an index, not a manual. Details live in one doc per topic under `docs/`.

- **Read:** before touching a topic, read its doc. Provider/API work → `docs/omni-provider-api.md`. Unity/build/deploy work → `docs/questdemo-build.md`. Product direction → `docs/omni-live-pitch.md`. Longer evidence → `docs/omni-live-research.md`. Binding design decisions → newest spec in `docs/superpowers/specs/`.
- **Update:** when behavior changes, update that topic's doc in the same commit as the code. A commit that changes the gateway, models, audit fields, or key lifecycle without touching `docs/omni-provider-api.md` is incomplete.
- **Create:** when a new topic emerges (new subsystem, new contract, new workflow), add `docs/<kebab-topic>.md` with the same shape: what it is, contract/rules, how to verify. Then add one line for it in the Layout list above.
- **Never:** duplicate a topic across two docs, paste secrets into any doc, or let AGENTS.md grow procedures that belong in a topic doc.

## Provider rules (hard)

- The API key lives **only** in the `YIBU_API_KEY` env var. Never in code, logs, Unity, or the repo.
- Every call carries a short `--purpose` label and is audit-logged to `provider/artifacts/yibu_api_calls.jsonl` (gitignored). Failures log too.
- Missing token counts stay `null` — unknown, never zero. No cost math in the tooling.
- Before any usage report: inspect `usage_raw` / `error` / `source.path` for leaks; send only the two summary files.
- Key expires Sep 20, 2026 8:00 AM EDT; summaries due Sep 20, 2026 11:59 PM EDT.

## Known gap: multi-turn + interruption accounting

- The vendored WebSocket scripts record **single-turn** usage only (Qwen: final `response.done`; Gemini: last `usageMetadata` before turn end).
- Multi-turn conversation and barge-in (TTS cancel, turn-taking) are planned. When building that: verify whether the provider reports per-response or cumulative usage per event, retain missing-usage indicators, dedupe on retry, and never double-count. Extend `yibu_audit.py` rather than working around it.
- Realtime rule: tool-call turns return arguments with **no audio** — execute the tool, return the result, then request the spoken follow-up.
- `qwen3.8-omni-flash` is text-out-only; voice needs a separate TTS stage.

## Verification

- Offline (no credit): `cd provider && . .venv/bin/activate && python -m unittest discover -s tests -v`
- Live smokes spend credit: tiny prompts, `--max-tokens 32`, distinct `--purpose`.
- Do not "fix" vendored example files to satisfy linters without checking upstream first.
