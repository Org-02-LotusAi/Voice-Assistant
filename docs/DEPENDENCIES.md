# Local dependency stack (Task 5)

Verified 2026-10-09 on Windows 11, Ryzen 5 5500U (12 threads), 15 GB RAM, CPU only.

## Pins
- Voicebox: https://github.com/jamiepine/voicebox @ `8af7efe62fab8d33e2a5dfbedabaa45f68a5184f` (v0.6.0, committed 2026-10-07), cloned to `deps/voicebox` (git-ignored).
- Python 3.12 venv at `deps/voicebox/.venv` (uv). torch 2.14.1+cpu, torchaudio 2.11.0+cpu (CPU wheel index; `-Device cuda` / `DEVICE=cuda` uses the cu128 index), transformers 4.57.3, numpy 1.26.4, numba 0.60.0, pedalboard 0.9.20, chatterbox-tts 0.1.7 (`--no-deps`).
- LiveKit: `livekit/livekit-server:v1.13.7` (published 2026-09-14), `docker-compose.dev.yml`, keys `devkey`/`secret`, ports 7880 (HTTP/WS), 7881 (TCP), 7882/udp (single mux), node-ip 127.0.0.1 (override with `LIVEKIT_NODE_IP` on EC2).

## What was installed (and what was not)
The full `backend/requirements.txt` FAILS on Windows: `kokoro` -> `misaki[ja]` -> `pyopenjtalk` does not build (8 min wasted before it errors). Fallback used and scripted: `scripts/voicebox-requirements.chatterbox-turbo.txt` = requirements.txt minus kokoro, misaki, unidic-lite, en_core_web_sm, linacodec, Zipvoice (LuxTTS) and the piper find-links. qwen-tts, conformer, diffusers, resemble-perth, s3tokenizer, pyloudnorm, pykakasi, spacy-pkuseg etc. ARE installed. Voicebox starts and serves Turbo with this set. `FULL=1` / `-Full` retries the complete file (may work on Linux).

## Run
- `scripts/setup_deps.ps1|.sh` once; `scripts/run_deps.ps1|.sh [start|stop|status]` (`-Warm` / `WARM=1` loads Turbo).
- Voicebox: `python -m backend.main --host 127.0.0.1 --port 17493 --data-dir deps/voicebox-store`; logs in `deps/logs/`, PID in `deps/voicebox.pid`.
- Seed profiles: `uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py [--generate]`.
- Profile ids in this install: Ava Warm `6326829e-38ae-475f-aedb-5c293aa43e51`, James Calm `1ae6b96a-8410-4455-af19-7c58bcdfbd94` (ids are per Voicebox database, not stable across reinstalls).
- LiveKit verified: `LiveKitAPI("http://127.0.0.1:7880","devkey","secret").room.list_rooms(...)` succeeds (livekit-api 1.2.1).

## Verified traps
1. **Voicebox data dir must not contain a path component named `data`.** `config._path_relative_to_any_data_dir` strips everything up to the first `data` part, so with `--data-dir <repo>/data/voicebox` the DB stores `voicebox\profiles\...`, which resolves to `data/voicebox/voicebox/profiles/...`: reference audio "not found", `/audio/{id}` 404, and cloning silently loses the reference. Default is therefore `deps/voicebox-store` (the brief's `data/voicebox` is NOT usable). `run_deps` refuses such paths.
2. Full requirements.txt does not install on Windows (pyopenjtalk), see above.
3. Turbo weights are 3.9 GB (`ResembleAI/chatterbox-turbo`, 3.8 GiB on disk), not ~1.5 GB: t3_turbo 1.9 GB, s3gen 1.0 GB, s3gen_meanflow 1.0 GB. First `POST /models/load` blocks ~7 min (about 3 min download + load). Cached load afterwards: 53-55 s.
4. A load can fail with `The paging file is too small (os error 1455)` when RAM is tight (other processes, WSL/Docker vmmem ~1-3 GB); retrying after memory freed worked. Budget >= 6 GB free for the Turbo process.
5. `GET /generate/{id}/status` is an SSE stream that blocks until the generation completes; `POST /generate` returns immediately. Clients must not use a short read timeout on status.
6. Paths with spaces: `Start-Process -ArgumentList` does not quote, so `--data-dir` needs embedded quotes (done in run_deps.ps1).
7. SAPI: "Microsoft Mark" is a OneCore voice, visible only to PowerShell 7 (`pwsh`), not Windows PowerShell 5.1. Seed voices use the "Desktop" Zira/David voices, available in both. SAPI output at 24 kHz is accepted for cloning (reference must be > 5 s; ours are 11.7 and 12.9 s).
8. Chatterbox sample rate is 24 kHz (`model.sr`); Moonshine wants 16 kHz, so test audio is 16 kHz and TTS output needs resampling for any STT loopback.
9. PyPI `chatterbox-tts==0.1.7` has no `nano` option; `nano=True` exists only on GitHub master (commit `5de7a54aa4e5e2baadb0182dde554908b48b85c2`, still reporting version 0.1.7). Voicebox (pinned) uses the PyPI code, so it cannot serve Nano as-is.

## Real-time factor (CPU, 12 threads, RTF = wall / audio; > 1 is slower than real time)
Sentence: "Hello, this is Ava. It is a pleasure to speak with you today, and I am glad to help."

| Path | Result |
|---|---|
| Voicebox `POST /generate` Turbo, Ava Warm, first call after load | wall 72.2 s, audio 5.36 s, RTF 13.5 |
| Voicebox `POST /generate` Turbo, second call | wall 54.8 s, audio 5.02 s, RTF 10.9 |
| In-process Turbo (full, GPT2_medium), run 1 / run 2 | RTF 11.7 / 8.7 (91.9 s / 50.6 s) |
| In-process Nano (110M, GPT2_small), run 1 / run 2 | RTF 3.30 / 3.32 (17.6 s / 18.2 s, ~5.4 s audio) |

Conditionals (`prepare_conditionals`) cost 6.6 s (Turbo) / 4.8 s (Nano) once per voice. Nano weights (`ResembleAI/chatterbox-nano`, 2.8 GB on disk incl. shared s3gen) exist and load (113 s cold from disk). Measurements were taken while the machine was shared with another worker, so absolute numbers are pessimistic, but the ratio is stable: Nano is ~3x faster than full Turbo and still ~3.3x slower than real time on this CPU. Conclusion: CPU-only Turbo cannot be conversational; the live-latency path needs a GPU (EC2) or accepts multi-second sentence latency on dev. To use Nano the engine sidecar (Task 6) must import chatterbox from GitHub master, not Voicebox's PyPI copy.

## Voice engine sidecar (Task 6)
`engine/chatterbox_server.py`, port 17494, run by `scripts/run_engine.ps1|.sh` with Voicebox's venv (see `engine/README.md`). Verified 2026-10-09, same laptop, CPU, 12 threads, shared with other workers (so numbers are noisy and pessimistic).

- **Chatterbox source**: the engine prefers GitHub master `resemble-ai/chatterbox@5de7a54aa4e5e2baadb0182dde554908b48b85c2`, cloned to `deps/chatterbox-src` (git-ignored, auto-cloned by the run scripts), for BOTH variants; it exposes `from_pretrained(device, nano=False)`. Voicebox itself keeps using its PyPI copy untouched. `turbo-nano` with a package lacking `nano` -> `/health` `status: error`, process exits 1 after `VOICE_ENGINE_FAIL_EXIT_S` (10 s). Verified fail-loud path with an invalid variant (health "error", exit code 1).
- Model load (weights cached): Turbo 34-55 s, Nano 21 s. The checkpoints ship built-in `conds.pt`, so the startup warm-up generate ("Hello there.") works without a profile. Resident RAM is heavy: free memory dropped to about 1.4 GB with Nano + Voicebox + Docker running; keep >= 6 GB free for Turbo.
- `/warm` for Ava Warm (reference 12.9 s): fetch 0.1-0.8 s, `prepare_conditionals` 3.8-4.3 s (Turbo) / 8.3 s (Nano, contended), tiny generate 10-20 s. Subsequent warm calls are instant (cached).
- Unknown profile detection: Voicebox `GET /profiles/{id}/samples` answers `200 []` for unknown ids, so the engine checks `GET /profiles/{id}` (404) first.

| Variant | Test | First chunk | Wall | Audio | RTF (wall/audio) |
|---|---|---|---|---|---|
| turbo | "Hello, this is Ava. It is a pleasure to speak with you." (2 chunks, pytest) | 19.6 s | 37.5 s | 4.24 s | 8.8 |
| turbo | "Hello, this is Ava. It is a pleasure ... glad to help." (2 chunks, solo) | 13.8 / 13.6 s | 34.0 / 34.8 s | 5.72 / 5.40 s | 5.95 / 6.45 |
| turbo-nano | same 2-chunk pytest text | 21.2 s (contended) | 33.5 s | 3.52 s | 9.5 |
| turbo-nano | long sentence, solo, run 1 / 2 | 8.9 / 6.2 s | 19.8 / 15.9 s | 5.12 / 5.04 s | 3.87 / 3.16 |

First-chunk latency is the time to the first PCM bytes after the request (profile already warm). On this CPU neither variant is realtime (Turbo ~6-9x slower than realtime, Nano ~3-4x); expect an order of magnitude better on a CUDA GPU, which is the production target. Streaming still helps: with sentence chunking the first audio arrives after roughly one clause instead of the whole utterance.

**Barge-in**: before each chunk the stream handler checks `request.is_disconnected()`; verified in the log (`chunks=1/4` after the client dropped after the first chunk). An in-flight `generate` cannot be aborted (the thread runs to completion), so a follow-up request waits for that one chunk (measured 15-31 s on CPU for a ~15 s chunk; one sentence on GPU).
