# AGENTS.md — Omni-Reality

Quest 3S spatial assistant for the Huawei OMNI Live track. Unity renders on-device; Python owns model I/O.

## Layout

- `QuestDemo/` — Unity native app (camera, depth, raycast, overlays stay on-device).
- `provider/` — yibuapi gateway client (Python). Setup/usage docs in `provider/README.md`.
- `docs/` — pitch + research brief (discussion, not approved design).

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
