# Omni provider base (yibuapi gateway)

Python entry points for the OMNI Live sponsor gateway, vendored from the
verified example pack `yibuapi_examples_20260918_v01` (real online calls OK
2026-09-18) plus a thin registry for this team's key.

## Enabled models (this key)

| Model | Transport | Script |
| --- | --- | --- |
| `qwen3.5-omni-flash` | HTTP Chat Completions | `qwen35_omni_flash.py` |
| `qwen3.5-omni-plus` | HTTP Chat Completions | `qwen35_omni_plus.py` |
| `qwen3.5-omni-plus-realtime` | WebSocket | `qwen35_omni_plus_realtime.py` |
| `qwen3.8-omni-flash` | HTTP Chat Completions (TEXT OUT ONLY — needs separate TTS) | `qwen38_omni_flash.py` |
| `gemini-3.1-flash-live-preview` | WebSocket (AUDIO out; text via transcription) | `gemini31_flash_live.py` |
| any other compatible ID | HTTP Chat Completions | `chat_completions_generic.py --model <id>` |

`models.py` is the single source of truth. `qwen38_omni_flash.py` is the only
wrapper not in the upstream pack (same HTTP shape as the other Qwen scripts).

## Setup

```bash
cd provider
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export YIBU_API_KEY='paste key in this shell only — never into a file that is committed'
```

Key facts: base URL `https://yibuapi.com/v1`; key expires **Sep 20, 2026 8:00 AM
EDT**; usage summaries due **Sep 20, 2026 11:59 PM EDT**. Scripts read the key
from `YIBU_API_KEY` only — creating a `.env` file alone does not configure
them. `.env.example` is a blank template. No key, prompt, or response body is
stored in the repo or the audit ledger (only a 4-char key suffix).

## Calls (label every call with --purpose)

```bash
python qwen35_omni_flash.py --prompt 'Introduce yourself in one sentence.' --purpose 'prototype_setup'
python qwen38_omni_flash.py  --prompt 'Say hi in one sentence.' --purpose 'smoke_qwen38'
python qwen35_omni_flash.py --prompt 'Describe the image.' --image /path/to.jpg --purpose 'image_understanding'
python qwen35_omni_plus_realtime.py --prompt 'Say hi briefly.' --purpose 'smoke_realtime'
python gemini31_flash_live.py --prompt 'Say hi briefly.' --purpose 'smoke_gemini_live'
python chat_completions_generic.py --model '<exact-id>' --prompt 'Hi.' --purpose 'smoke_generic'
```

No-proxy is enforced in code (`httpx` `trust_env=False`, websockets
`proxy=None`), so `HTTP(S)_PROXY` env vars are ignored.

## Usage tracking (mandatory for the report)

Every call appends to `artifacts/yibu_api_calls.jsonl` (gitignored). Override
per-call with `--audit-log <path>` or via `YIBU_AUDIT_LOG`. Missing token
values stay `null` — unknown, not zero.

```bash
python summarize_usage.py
python summarize_usage.py --log /path/to/yibu_api_calls.jsonl --out-dir /path/to/summary
```

Outputs `usage_summary.json` + `usage_by_model_key_purpose.csv`. Inspect both
before sending; `usage_raw`/`error` fields and `source.path` can leak local
details. Report by replying to the key-delivery email with team name, repo
link, application email, period, and key suffix(es). Keep the JSONL ledger
private unless organizers request it.

## Offline checks (no credit spent)

```bash
. .venv/bin/activate
python -m compileall -q .
python -m unittest discover -s tests -v
```

## Notes for Unity integration (later commit)

Python owns model I/O + audit. Unity will POST text/image/audio and receive
text + scene ops; no API key ever enters Unity or the repo. Realtime detail:
tool-call turns return arguments without audio — run the tool, return the
result, then request the spoken follow-up.
