# Omni provider API (yibuapi gateway)

Everything an agent needs to use the sponsor model gateway. Code lives in `provider/`; setup synopsis in `provider/README.md`. This doc is the why and the contract.

## Gateway

- OpenAI-compatible HTTP gateway at `https://yibuapi.com/v1`, plus two WebSocket routes (below).
- Vendor example pack this is built from: `yibuapi_examples_20260918_v01` (verified with real online calls 2026-09-18). Vendored files are byte-for-byte upstream — do not "fix" them for linters without checking upstream first.
- No-proxy is enforced in code: HTTP uses `httpx.Client(trust_env=False)`, WebSockets use `proxy=None`. `HTTP(S)_PROXY` env vars are ignored by design.
- Setup/usage-reporting guide (English): `docs/yibuapi-usage-reporting.md` upstream link is in the key-delivery email; the rules below summarize it.

## Credentials

- Key is read from the `YIBU_API_KEY` env var **only**. Scripts do not load `.env` files. `provider/.env.example` is a blank template.
- Never commit, log, or display the key. The audit ledger stores only a 4-char suffix (`...xPvc` for the current key). Error strings are redacted (`[REDACTED]`) before logging.
- Key expiry: **Sep 20, 2026 8:00 AM EDT**. Usage summaries due: **Sep 20, 2026 11:59 PM EDT** (reply to the key-delivery email, attach the two summary files, include team name, repo link, application email, period, key suffix).

## Models (current key)

`provider/models.py` is the registry. Confirmed live via `/v1/models` on 2026-09-19.

| Model | Transport | Endpoint | Script | I/O notes |
| --- | --- | --- | --- | --- |
| `qwen3.5-omni-flash` | HTTP Chat Completions | `/v1/chat/completions` | `qwen35_omni_flash.py` | text/image/audio in, text out |
| `qwen3.5-omni-plus` | HTTP Chat Completions | `/v1/chat/completions` | `qwen35_omni_plus.py` | text/image/audio in, text out |
| `qwen3.8-omni-flash` | HTTP Chat Completions | `/v1/chat/completions` | `qwen38_omni_flash.py` | multimodal in, **TEXT OUT ONLY** — needs separate TTS for voice |
| `qwen3.5-omni-plus-realtime` | WebSocket | `wss://yibuapi.com/v1/realtime?model=...` (Bearer header) | `qwen35_omni_plus_realtime.py` | single-turn text in the example |
| `gemini-3.1-flash-live-preview` | WebSocket | `wss://yibuapi.com/v1beta/gemini/live` (Bearer header) | `gemini31_flash_live.py` | returns **AUDIO**; text via `outputAudioTranscription`; `--audio-out` saves raw PCM bytes |
| any other compatible ID | HTTP Chat Completions | `/v1/chat/completions` | `chat_completions_generic.py --model <exact-id>` | text only in the generic wrapper |

## HTTP call shape

- `yibu_http.build_omni_messages(prompt, image=..., audio=...)` builds OpenAI-style content parts: `text`, `image_url` (data URL), `input_audio` (`{"data": data-url, "format": ext}` — the shape the successful upstream calls used).
- `chat_completion(...)` POSTs `{model, messages, max_tokens, temperature}`, extracts `choices[0].message.content` (string or list-of-parts), and audit-logs before returning.
- Keep smoke prompts to one sentence with `--max-tokens 32` to minimize spend.

## WebSocket flows

**Qwen realtime** (`qwen35_omni_plus_realtime.py`): connect → expect `session.created` (model must match request) → `session.update` (`modalities: ["text"]`, no turn detection in the example) → `conversation.item.create` (user message) → `response.create` → collect `response.text.delta` / `response.audio_transcript.delta` → `response.done` (usage source). Any `type: error` event raises.

**Gemini Live** (`gemini31_flash_live.py`): connect → send `setup` (`model: models/<id>`, `responseModalities: ["AUDIO"]`, `outputAudioTranscription: {}`) → wait `setupComplete` → send `clientContent` (`turnComplete: true`) → accumulate `serverContent.outputTranscription.text` (+ `modelTurn` fallback text, `inlineData` audio chunks) and latest `usageMetadata` → stop at `serverContent.turnComplete`.

**Realtime tool rule** (upstream SDK): a tool-call turn returns arguments with **no audio**. Execute the tool, send the result back, then request the spoken follow-up. Unity owns the drawing; the model chooses ops.

## Usage accounting (mandatory)

- Every wrapper call appends one JSONL record to `provider/artifacts/yibu_api_calls.jsonl` (gitignored) — successes and failures. Override location per-call with `--audit-log <path>` or via `YIBU_AUDIT_LOG` (explicit flag wins).
- Record fields: `call_id`, `timestamp_utc`/`timestamp_local`, `provider`, `model`, `key_suffix`, `purpose`, `transport`, `endpoint`, `ok`, `status_code`, `latency_s`, normalized `input_tokens`/`output_tokens`/`total_tokens` (+ `total_tokens_derived`, `usage_reported`, raw `usage_raw`), redacted `error` on failure. No prompts, media, replies, or full keys — verified by leak check.
- Normalization covers OpenAI HTTP (`prompt_tokens`/`completion_tokens`/`total_tokens`), OpenAI realtime nested `response.usage` (`input_tokens`/`output_tokens`), and Gemini (`promptTokenCount`/`responseTokenCount`/`totalTokenCount`, incl. `inputTokenCount` variants). Total is derived only when both parts exist and is flagged.
- **Missing stays `null`: unknown, never zero.** Summaries sum reported values and carry `*_missing_calls` counters.
- Summarize: `python summarize_usage.py [--log <ledger> --out-dir <dir>]` → `usage_summary.json` + `usage_by_model_key_purpose.csv` (grouped by model/key/purpose, ok/failed splits). Identical duplicate call IDs are ignored; conflicting duplicates raise.
- Before reporting: inspect both files. `usage_raw`, `error`, `source.path` (absolute local path), purpose labels, and endpoints can leak details — redact a sharing copy, keep the original ledger private.

## Observed live numbers (2026-09-19 smoke, minimal prompts)

| Call | Latency | Tokens |
| --- | --- | --- |
| qwen3.5-omni-flash | 0.59s | 19 / 4 / 23 |
| qwen3.5-omni-plus | 1.30s | 19 / 3 / 22 |
| qwen3.8-omni-flash | 1.03s | 62 / 37 / 99 |
| qwen3.5-omni-plus-realtime (first WS session) | 12.49s | 31 / 5 / 36 |
| gemini live attempt 1 | 9.49s, transient upstream `1011 / UPSTREAM_CLOSED / resource exhausted` | unknown |
| gemini live retry | 2.20s ok | 147 / 39 / 186 |

Takeaways: realtime first-turn setup is slow (~12s — budget for session warm-up); Gemini Live threw one transient upstream failure then succeeded (retry before concluding quota issues); Live token overhead is high (147 in / 39 out for a one-liner).

## Audio input (2026-09-19 voice smoke, `qwen3.8-omni-flash`)

Mic PCM is wrapped as WAV (16 kHz mono s16le) and sent as an `input_audio` data URL (`voice/audio.py: build_voice_messages`). This default shape works; `raw_b64` was not needed. Test speech comes from macOS `say` (`python -m voice.audio_smoke --say ...`).

| Call | Result | Latency | Tokens in / out (reasoning) |
| --- | --- | --- | --- |
| audio only, 1.82 s (58 KB WAV) | heard verbatim: "Mark the red bottle on the left." | 6.02s | 103 (9 audio) / 523 (492) |
| control, `--no-audio` | invented speech and an image that were never sent | 7.71s | 94 / 796 (765) |
| audio 1.23 s + JPEG 612x408 (34 KB) | heard verbatim; described image correctly | 2.45s | 352 (9 audio, 249 image) / 106 (71) |
| full voice turn via coordinator + fake Quest | `mark` image_point u=0.62 v=0.72 (on the laptop), ACK placed, spoke | ~3.5s model | — |

Takeaways:

- Audio really reaches the model: exact transcripts with audio, fabrication without. Slice-3 "mic audio in the HTTP call" is met.
- `qwen3.8-omni-flash` does hidden reasoning that dominates latency (up to ~765 reasoning tokens) and `max_tokens` did not cap it (64 requested, 523 returned). Trying to disable thinking is the next latency lever.
- With no image the model may invent one. The planner adds no ops when no JPEG was sent, whatever the model says.
- The model said "I've marked…" despite the prompt. The coordinator only speaks that line after an ACK `placed`, so the claim stays honest.

## LAN coordinator voice planners (`coordinator/server.py`)

The offline LAN WebSocket coordinator can run voice turns with `--planner stub|yibu|voice-stub` (plus the legacy `--planner mark` hardcoded mark path). These modes sit beside the grounded voice path above: `--planner yibu` without `--voice-only` still sends JPEG when Quest provides a frame, binds Omni worker tools, and may emit up to three scene ops after ACKs. The bootstrap modes below prove mic transport or live audio-only conversation without spatial ops.

| CLI | Credit | Model / speech | Scene ops | Audit `purpose` labels |
| --- | --- | --- | --- | --- |
| `--planner voice-stub` | none | Fixed caption plus a deterministic **non-speech** 16 kHz mono PCM test tone (`voice/test_tone.py`) — transport check only, not Omni speech | none | none (no yibu calls) |
| `--planner yibu --voice-only` | yes (plan + TTS) | Buffered mic PCM → WAV → `qwen3.8-omni-flash` HTTP omni **without** JPEG or tools; reply text → Gemini Live cloud PCM for playback | none | `voice-only-turn` (omni), `voice-only-speak` (TTS) |
| `--planner yibu` (default voice) | yes | PCM + optional JPEG, tool loop, spatial prompts | up to 3 after ACK | `voice-turn`, `voice-speak` |

`--voice-only` is rejected unless `--planner yibu`. Direct `YibuPlanner(voice_only=True)` defaults `purpose` to `voice-only-turn`; grounded turns keep `voice-turn`. Live backends load lazily inside `plan()` / synthesizer — offline unit tests inject fakes and spend no credit.

## Known gap: multi-turn + interruption

The WS scripts record single-turn usage only and have no barge-in handling. Planned conversation work needs: session reuse across turns, per-event usage classification (per-response vs cumulative — verify, never double-count), missing-usage retention, retry dedupe, TTS cancellation on interruption, and stale-frame pose discipline (late replies use capture-time pose). Extend `yibu_audit.py`; see AGENTS.md doc procedure before adding new scripts.

## Verification

- Offline: `cd provider && . .venv/bin/activate && python -m compileall -q . && python -m unittest discover -s tests -v` (9 tests, no credit).
- Ledger leak check: from `provider/`, run `grep -c 'sk-' artifacts/yibu_api_calls.jsonl` — it must print 0 (no key material), and spot-check that no prompt or reply text appears in the ledger.
