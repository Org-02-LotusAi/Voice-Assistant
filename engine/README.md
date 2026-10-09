# Voice engine sidecar (Chatterbox Turbo, sentence-level streaming)

A small FastAPI app run with **Voicebox's venv interpreter** (torch + chatterbox live there). It never imports `service015`.
It loads one copy of Chatterbox Turbo (or Turbo-Nano) and serves speech for Voicebox *cloned* profiles over HTTP.

```
scripts/run_engine.ps1 [-Action start|stop|status] [-Variant turbo|turbo-nano] [-Device cpu|cuda]
scripts/run_engine.sh  [start|stop|status]      # CHATTERBOX_VARIANT / VOICE_ENGINE_DEVICE env
```
Port 17494. Logs `deps/logs/engine.log` / `engine.err.log`, PID `deps/engine.pid`. Model load takes about a minute on CPU
(`GET /health` shows `status: loading` until done).

## Config (env)
| var | default | |
|---|---|---|
| `VOICE_ENGINE_DEVICE` | `cpu` | passed to `from_pretrained` as a **string** |
| `CHATTERBOX_VARIANT` | `turbo` | `turbo` or `turbo-nano` |
| `VOICEBOX_URL` | `http://127.0.0.1:17493` | source of reference audio |
| `VOICE_ENGINE_HOST` / `VOICE_ENGINE_PORT` | `127.0.0.1` / `17494` | |
| `CHATTERBOX_SRC` | `deps/chatterbox-src/src` if present | chatterbox package dir to prefer on `sys.path` |
| `VOICE_ENGINE_FAIL_EXIT_S` | `10` | on startup failure `/health` reports `error` for this long, then exit code 1 |

**Nano needs GitHub master.** PyPI `chatterbox-tts==0.1.7` (what Voicebox installs) has no `nano` option. The run scripts clone
`resemble-ai/chatterbox` at `5de7a54aa4e5e2baadb0182dde554908b48b85c2` into `deps/chatterbox-src` (git-ignored) and the engine
prefers it for both variants. If `turbo-nano` is requested and the importable package lacks `nano`, startup fails loudly
(`/health` -> `status: error`, then exit 1); there is no silent fallback to full Turbo.

## API
- `GET /health` -> `{status: ok|loading|error, engine: "chatterbox-turbo", variant, device, sample_rate: 24000, warm, cached_profiles, nano_supported, load_seconds, error}`
- `POST /warm {"profile_id"}` -> fetches the profile's samples from Voicebox, takes the longest one > 5 s (else 422 `reference_too_short`),
  `prepare_conditionals`, caches the conditionals (LRU, 8 profiles), runs one tiny generate. Returns timings. 404 `profile_not_found`, 503 `voicebox_unavailable`.
- `POST /synthesize/stream {"profile_id","text"}` -> `audio/pcm` (s16le, mono, 24 kHz), header `X-Sample-Rate: 24000`
  (also `X-Chunk-Count`). Text is split into clauses/sentences (`textsplit.py`: a long first sentence is cut at its first natural clause
  so the first audio comes sooner; tiny fragments are merged; no chunk > 220 chars). Each chunk's PCM is sent as soon as it is generated.
  Unprepared profiles are warmed inline first. Errors as for `/warm`; 503 `engine_loading` / `engine_error` while not ready.
- `POST /preview {"profile_id","text"}` -> the same synthesis path, returned as one `audio/wav`.

Errors are `{"code": ..., "detail": ...}` JSON.

## Concurrency and barge-in
All model calls run on one worker thread under one lock, so requests are served serially (Chatterbox is not re-entrant, and the
conditionals are swapped onto the model per chunk). Before every chunk the stream handler checks `await request.is_disconnected()`;
if the client has gone, remaining chunks are not generated. **An in-flight chunk cannot be aborted** (a single `generate` call is
uninterruptible), so a new request waits at most one chunk (one sentence) behind an abandoned stream. Keep chunks short for that reason.

## Logs
One line per request: `synth profile=... chunks=done/total first_chunk_s=... audio_s=... wall_s=... rtf=...`. Audio is never logged.

## Test
`VOICE_ENGINE_URL=http://127.0.0.1:17494 uv run pytest tests/engine -v -s` (live tests skip when the engine is not reachable;
`tests/engine/test_textsplit.py` always runs). If another worker's code breaks `tests/conftest.py`, add `--confcutdir=tests/engine`.
Measured latency/RTF: `docs/DEPENDENCIES.md`.
