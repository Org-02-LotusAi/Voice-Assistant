# SAFI Service 015: AI Voice Identity Workbench

Service 015 turns a **Template** (an archetype such as Sales Representative) into persistent **Identities** (Ava, James,
...), each a named AI voice participant that lives in its own LiveKit **Default Room**, speaks with a cloned Voicebox
voice, and always knows who it is (authoritative self-facts that persona text cannot override). A Meeting Manager platform
launches an Identity, lets humans (browser token) or phones (via Service 014) into its room, and reads presence. A small
Workbench UI creates and tests Identities; a scoped Service Expert answers questions about the service itself.

Authoritative documents: `docs/SOW.md` (the requirements), `openapi.yaml` (the API contract, served at `GET /openapi.yaml`),
`docs/PLAN.md` (design and decisions), `docs/INTEGRATION.md` (platform guide), `docs/ACCEPTANCE.md` (test matrix),
`docs/DEPENDENCIES.md` (pins, traps, measurements), `deploy/EC2.md` (GPU deployment), `docs/PHONE_DOGFOOD.md` (phone runbook).

## Architecture

```
Workbench UI / Meeting platform ---REST---> service015 API (FastAPI :8015, SQLite)
                                               |  launch = LiveKit explicit dispatch (agent "service015-identity",
                                               |           room = default_room, metadata {"identity_id"})
                                               v
LiveKit server (:7880 ws, :7881 tcp, :7882 udp) <---- runtime worker (spawned and supervised by the API)
   ^                                                    per Identity job: load Identity from SQLite -> fresh authoritative
   |                                                    instructions; Moonshine STT -> LLM (OpenAI-compatible, Groq)
 human (browser token)                                  -> Chatterbox TTS plugin --HTTP--+
 phone (SIP via Service 014, optional)                                                     v
                                          voice engine sidecar (:17494; torch + Chatterbox Turbo, sentence streaming PCM)
                                                   | reference samples
                                                   v
                                          Voicebox headless backend (:17493): voice profiles, samples, cloning
```

Repo map: `src/service015/` (api, runtime, voice, stt, telephony, expert, ui, migrations, starter_templates), `engine/`
(voice engine sidecar), `scripts/` (setup, run, seed, acceptance), `tests/`, `deploy/`, `docs/`, `assets/` (synthetic seed
voices and test audio).

Legend for the commands below: **[verified]** = run successfully on the Windows 11 development machine during this
project; **[unverified]** = written from the scripts and docs but not executed (Linux, Docker GPU, EC2).

## Requirements

Python 3.12 via [`uv`](https://docs.astral.sh/uv/), Docker (for LiveKit), git. For real voice conversation: about 6 GB
free RAM for the TTS engine, an OpenAI-compatible LLM key (`LLM_API_KEY`, Groq by default). CPU-only machines work but
synthesis is far slower than real time (see `docs/DEPENDENCIES.md`); a CUDA GPU is the production target.

## Quickstart: Windows (PowerShell) [verified]

```powershell
git clone <this repo> ; cd <repo>
uv sync                                         # service015 venv + dev tools
copy .env.example .env                          # then set LLM_API_KEY and the SERVICE015_API_KEYS keys

# 1. Setup deps once: clones Voicebox at the pinned commit, torch (CPU), minimal Chatterbox set, pulls the LiveKit image
powershell -File scripts/setup_deps.ps1                      # add  -Device cuda  for an NVIDIA GPU

# 2. Run deps: LiveKit dev server (Docker) + Voicebox headless on :17493. -Warm loads Chatterbox Turbo (first run downloads 3.9 GB)
powershell -File scripts/run_deps.ps1 -Action start -Warm

# 3. Run the voice engine on :17494 (poll /health until "status":"ok"; about a minute on CPU)
powershell -File scripts/run_engine.ps1 -Action start        # -Variant turbo-nano is ~3x faster on CPU, lower quality
curl http://127.0.0.1:17494/health

# 4. Seed the two synthetic voices in Voicebox, then record their authorizations in service015's DB
uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py
uv run python scripts/authorize_seed_voices.py

# 5. Run the API (this also spawns the runtime worker because LIVEKIT_URL/KEY/SECRET are set in .env)
uv run service015-api                                         # http://127.0.0.1:8015, GET /health shows runtime: ok

# 6. Open the Workbench (paste an operator key from SERVICE015_API_KEYS) and the Talk page
start http://127.0.0.1:8015/
```

Stop: Ctrl+C the API (stops the worker), `scripts/run_engine.ps1 -Action stop`, `scripts/run_deps.ps1 -Action stop`.
Seed voices can be regenerated with `scripts/make_seed_voices.ps1` (Windows SAPI only; committed WAVs already exist).

## Run the local demo (Windows laptop) [verified]

Prerequisites: Docker Desktop running, `uv sync` done, `.env` copied from `.env.example` with `LLM_API_KEY` and
`SERVICE015_API_KEYS` set, `scripts/setup_deps.ps1` run once. Then, in order:

```powershell
powershell -File scripts/run_deps.ps1 -Action start -Warm        # LiveKit + Voicebox (:17493)
powershell -File scripts/run_engine.ps1 -Action start            # Chatterbox Turbo engine (:17494); wait for "status":"ok"
curl http://127.0.0.1:17494/health
uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py   # first time only
uv run python scripts/authorize_seed_voices.py                   # authorize the seed voices
uv run service015-api                                            # API + runtime worker on :8015
curl http://localhost:8015/health                                # runtime, voicebox, voice_engine all "ok"
```

Then in the browser:

1. Open http://localhost:8015 and paste an operator key (an `operator` or `admin` entry of `SERVICE015_API_KEYS`).
2. Create Ava: pick the **Sales Representative** template, set name Ava (company and role as you like), choose the voice
   **Ava Warm**, press **CREATE IDENTITY**.
3. Press **LAUNCH** and wait for the status to reach READY (about a minute on CPU).
4. Press **TEST** to open the talk page, allow the microphone and speak. On this CPU each reply takes 15-20 s; that is the
   documented latency limit, not a hang.

Verified on this host: the API starts, `/health` reports `database`, `runtime`, `voicebox` and `voice_engine` ok, and `/`
serves the Workbench (CREATE IDENTITY, LAUNCH, TEST controls present). The create/launch/talk flow itself is what the
acceptance run exercises (`docs/ACCEPTANCE.md`). Stop with Ctrl+C on the API, then `scripts/run_engine.ps1 -Action stop` and
`scripts/run_deps.ps1 -Action stop`.

## Quickstart: Linux [unverified]

```bash
git clone <this repo> && cd <repo>
uv sync
cp .env.example .env                            # set LLM_API_KEY and keys
DEVICE=cpu scripts/setup_deps.sh                # DEVICE=cuda for a GPU host; FULL=1 tries Voicebox's full requirements
WARM=1 scripts/run_deps.sh start
scripts/run_engine.sh start                     # CHATTERBOX_VARIANT / VOICE_ENGINE_DEVICE from the environment
curl http://127.0.0.1:17494/health
uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py
uv run python scripts/authorize_seed_voices.py
uv run service015-api
xdg-open http://127.0.0.1:8015/
```

The Workbench needs an operator API key. To drive the API without the UI, see `docs/INTEGRATION.md` (curl walkthrough).
Browsers allow the microphone on `http://localhost` and on https only, so a remote host needs TLS (`deploy/EC2.md`).

## Tests

```bash
# Unit + contract + mocked integrations (offline; the live-service tests skip themselves) [verified]
uv run pytest tests -q --ignore=tests/test_runtime_live.py --ignore=tests/e2e

# Live service tests: skipped unless the service is reachable / the key is set [verified]
VOICE_ENGINE_URL=http://127.0.0.1:17494 uv run pytest tests/engine -v -s          # engine (needs run_engine)
uv run pytest tests/test_expert_live.py -v                                         # needs LLM_API_KEY
uv run pytest tests/test_runtime_live.py -s -q                                     # one real conversation: needs LiveKit, Voicebox, engine, LLM; not alongside the acceptance run

# End-to-end acceptance with real audio and evidence (SOW tests 4-11, 13, 27); see docs/ACCEPTANCE.md
uv run python scripts/run_acceptance.py --allow-latency-fail      # CPU host: a missed latency ceiling is reported, not failed
uv run python scripts/run_acceptance.py                           # GPU reference host: the SOW latency ceiling is enforced
```

On Windows PowerShell set the engine URL with `$env:VOICE_ENGINE_URL="http://127.0.0.1:17494"` first.

## Service Expert (SOW 23-24)

```bash
uv run service015-expert ingest                       # (re-)ingest the manifest documents into the configured DB; run from the repo root [verified]
uv run service015-expert ask "Which roles can launch an identity?"   # needs LLM_API_KEY [verified: fails cleanly without a key]
# TEST 15: ingest one verified fact, ask it in a fresh session, write evidence/expert_fresh_learning.json
uv run python scripts/expert_fresh_learning.py --title "..." --body "..." --source "path::test" \
    --question "..." --expect "text that must appear in the answer"      # needs LLM_API_KEY; exit 0 PASS / 1 FAIL / 2 no LLM
```

HTTP: `POST /expert/experience` (admin), `POST /expert/sessions` and `POST /expert/sessions/{id}/ask` (viewer).

## Docker Compose [partly verified]

```bash
cp .env.example .env          # set keys; COMPOSE_PROFILES=cpu is the default in .env.example
docker compose --profile cpu up -d --build     # livekit + redis + voicebox + engine (CPU) + service015
docker compose --profile gpu up -d --build     # GPU engine/voicebox (NVIDIA container toolkit required)
docker compose --profile sip up -d             # add livekit/sip (Linux only, host networking) for Service 014 testing
docker compose --profile tls up -d             # add Caddy (https/wss) for a public host; see deploy/EC2.md
docker compose ps && curl http://127.0.0.1:8015/health
```

Verified here: `docker build -t service015 .` (non-root, healthy), the `service015` container serving `/health`, the UI and
`/openapi.yaml` and spawning its runtime worker against a LiveKit container using `deploy/livekit.yaml`, and `docker compose
config` for every profile. Not verified: the full CPU stack start, the CUDA image, the `sip` and `tls` profiles, and anything
on EC2. See `deploy/EC2.md` for the GPU deployment and `docker-compose.yml` for the ports, volumes and the
`LIVEKIT_PUBLIC_URL` split (server-side `ws://livekit:7880` vs the URL browsers receive).

## Configuration

Everything is environment-driven; `.env.example` lists every variable with comments. `.env` is git-ignored: never commit it.
Roles for `SERVICE015_API_KEYS`: `admin` > `operator` > `viewer`. The LLM, STT and TTS choices are internal; the platform
contract is `openapi.yaml`.

## Known limits

- CPU-only hosts cannot meet the SOW latency target (TTS is 6-9x slower than real time on a 12-thread laptop CPU); see
  `docs/ACCEPTANCE.md` and `docs/DEPENDENCIES.md`. The GPU measurement is pending.
- TEST 12 (real phone through Service 014 + Exotel) is blocked on external systems; the adapter is mocked-tested and the
  runbook is `docs/PHONE_DOGFOOD.md`. The Service 014 HTTP contract in `HttpBackend` is an assumption.
- One API process per database (per-process launch lock). One runtime worker serves all Identities, capped by
  `SERVICE015_MAX_ACTIVE_IDENTITIES` (default 2).
- **LLM rate limit.** The default Groq on-demand tier allows about 7,000 input tokens per minute for `qwen/qwen3.8-27b`. One
  reply request carries about 1k tokens (instructions plus the last `SERVICE015_MAX_HISTORY_TURNS` turns), so the whole key
  serves roughly 1 conversation, 2 at light use (about 6 requests per minute in total). The Service Expert shares the same
  `LLM_API_KEY` and budget. Upgrading the Groq tier, or pointing `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` at another
  OpenAI-compatible endpoint, raises this (then raise `SERVICE015_MAX_ACTIVE_IDENTITIES` too). A 429 shows as
  `status_detail` `llm_rate_limited` (not `llm_unavailable`); the session backs off (honoring `Retry-After`) and is recreated
  up to `SERVICE015_SESSION_RECOVERY_ATTEMPTS` times, never replying with invented speech. Speculative generation is off
  (`SERVICE015_PREEMPTIVE_GENERATION=false`) and Moonshine merges the lines of one utterance into one transcript.
- **Logs and privacy.** Transcripts and per-event tracing are logged at DEBUG only. `SERVICE015_RUNTIME_LOG` (default INFO) sets
  the level of a size-rotated `runtime-worker.rotating.log` next to the database (`SERVICE015_RUNTIME_LOG_MAX_BYTES`,
  `SERVICE015_RUNTIME_LOG_BACKUPS`); the per-spawn `runtime-worker.log` stays at INFO or above. Turn text in `turn_metrics`
  is blanked after `SERVICE015_TRANSCRIPT_RETENTION_DAYS` (default 30) at API startup; timings are kept.
- Phone dialing: set `SERVICE014_ALLOWED_DESTINATIONS` (E.164 prefixes). Empty means deny all on a non-local
  `SERVICE015_PUBLIC_URL`, allow all on localhost.
- Voicebox's full requirements do not install on Windows (pyopenjtalk); the scripts use the minimal Chatterbox set.

## Release

Code SHA the acceptance evidence was produced at: `c806c9ead216aef1c8ed0824e296ebe55c405428` (`c806c9e`); accepted run `evidence/20261010T153557Z/`. See `docs/ACCEPTANCE.md`.
