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
  (also `X-Chunk-Count`, `X-Request-Id`). Text is split into clauses/sentences (`textsplit.py`: first chunk ~40 chars, cut at a clause
  break else a word boundary; tiny fragments are merged; no later chunk > 120 chars). Each chunk's PCM is sent as soon as it is generated.
  Unprepared profiles are warmed inline first. Errors as for `/warm`; 503 `engine_loading` / `engine_error` while not ready.
- `POST /preview {"profile_id","text"}` -> the same synthesis path, returned as one `audio/wav`.

Errors are `{"code": ..., "detail": ...}` JSON.

## Concurrency and barge-in
All model calls run on one worker thread under one lock, so requests are served serially (Chatterbox is not re-entrant, and the
conditionals are swapped onto the model per chunk). Before every chunk the stream handler checks `await request.is_disconnected()`;
Starlette also cancels the generator when the client goes away. Either way remaining chunks are not generated and the request ends
as `cancelled`.

**An in-flight chunk cannot be aborted.** Chatterbox's `inference_turbo` is a hand-written token loop with no callback,
stopping criteria or injectable processor (the logits processors are locals), and we do not monkeypatch private internals. The wait
is bounded by chunk size instead: first chunk about 40 chars (cut at a clause break <= 45 chars, else at a word boundary), later
chunks at most 120 chars. Worst-case wait for a request that arrives right after a barge-in is therefore one 120-char chunk
(about 8 s of audio): ~50 s on this CPU (RTF 6), roughly 1 s on a GPU. Clients should not wait for the abandoned stream; just
issue the next request (it queues behind at most that one chunk).

## Detecting a failed or truncated stream (clients: read this)
Errors before the first byte are plain HTTP errors: JSON `{"code","detail"}` with status 404 `profile_not_found`, 422 `reference_too_short`
/ `corrupt_reference` / `invalid_request` / `empty_text`, 503 `voicebox_unavailable` / `engine_loading` / `engine_error`, 500 `synthesis_failed`.

Once the 200 and headers are sent, an error cannot change the status. The engine then logs `stream failed ... code=<code>`, records it,
and **aborts the response without the terminating chunk**. Clients detect this as follows (all three agree):
1. The HTTP read ends abnormally (httpx `RemoteProtocolError`/`ReadError`, aiohttp `ClientPayloadError`). Treat as failure.
2. Every response carries `X-Request-Id` and `X-Chunk-Count` (set before streaming). After the stream ends (or breaks), call
   `GET /requests/{request_id}` -> `{request_id, profile_id, state: streaming|complete|cancelled|failed, chunks_total, chunks_done, code, detail}`.
   The stream was complete only if `state == "complete"` (`chunks_done == chunks_total`). On `failed`, `code` is one of
   `voicebox_unavailable`, `reference_too_short`, `corrupt_reference`, `profile_not_found`, `synthesis_failed`.
3. Statuses are kept for the last 256 requests (404 `request_not_found` afterwards).
`/preview` returns the same coded JSON error (no bare 500) because nothing has been sent yet.

## Logs
One line per request: `synth profile=... chunks=done/total first_chunk_s=... audio_s=... wall_s=... rtf=...`. Audio is never logged.

## Test
`VOICE_ENGINE_URL=http://127.0.0.1:17494 uv run pytest tests/engine -v -s` (live tests skip when the engine is not reachable;
`tests/engine/test_textsplit.py` always runs). If another worker's code breaks `tests/conftest.py`, add `--confcutdir=tests/engine`.
Measured latency/RTF: `docs/DEPENDENCIES.md`.

Notes: `profile_id` must match `^[A-Za-z0-9_-]{1,64}$` (it is placed in a Voicebox URL path). If the checkpoint ships no built-in conditionals, `/health` is `ok` with `warm:false` until the first `/warm`.
