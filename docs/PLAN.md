# Service 015 — Implementation Plan

Source of truth: `S015_AI_Voice_Identity_Workbench.docx` (the SOW). This plan only
decides HOW; whenever this plan and the SOW disagree, the SOW wins.

## Global Constraints (binding on every task)

- Python **3.12** exactly, managed with **uv** (`uv run ...`). Package `service015` under `src/service015/`. Tests under `tests/` with **pytest**.
- Standalone repo (`VoiceSOW/`, branch `main`). Never touch sibling folders in `Lotus Interworks/`.
- Secrets only via environment / `.env` (git-ignored). `.env.example` documents every variable. Never log secrets or raw voice samples.
- Persistence: **SQLite** (`SERVICE015_DB_PATH`, default `./data/service015.db`), WAL mode, schema created by ordered SQL migrations in `src/service015/migrations/NNN_*.sql`, tracked in table `schema_migrations`.
- `openapi.yaml` (repo root, OpenAPI 3.1) is THE contract. Implementation must conform exactly; a test validates the spec (openapi-spec-validator) and a conformance test checks every spec operation exists in the FastAPI app and responses validate against the spec schemas.
- Invariants (SOW §5): Template ≠ Identity ≠ Runtime Process. Template update never mutates an existing Identity. Identity is bound to the exact template version used. Self-facts are authoritative; persona/prompt text can never override them.
- Identity ids: `<slug(name)>-<NNN>` (e.g. `ava-001`, NNN = next free 3-digit counter per slug). `participant_identity == default_room == identity_id`, fixed at instantiation, never regenerated.
- Presence enum: `READY | STARTING | BUSY | ERROR | OFFLINE` (plus `RECONNECTING` allowed only as ERROR sub-state in `status_detail`).
- Errors: every non-2xx JSON body is `{"error": {"code": "<snake_case>", "message": "<human text>", "details": {...}}}`.
- Auth: every route except `GET /health`, the static UI and `/openapi.yaml` requires `Authorization: Bearer <key>`. Keys from env `SERVICE015_API_KEYS="key1:admin,key2:operator,key3:viewer"`. Roles: `admin` ⊃ `operator` ⊃ `viewer`. Admin: create templates/versions, record voice authorizations, ingest expert experience. Operator: instantiate/patch/launch/stop/join-token/phone. Viewer: all GETs, expert ask. 401 for missing/bad key, 403 for insufficient role.
- Out of scope (SOW §30): no video, OBS, meeting manager, participant movement, SIP/PSTN code, CRM, model training, fancy UI, template auto-upgrade.
- Minimal diff, house style: type hints, pydantic v2 models, no swallowed exceptions, no fake fallback data.

## Architecture

```
src/service015/
  config.py            Settings (pydantic-settings, env)
  db.py                sqlite connect + migrate
  migrations/001_init.sql
  models.py            pydantic models (Template, Identity, Resolved, Status, errors)
  starter_templates/   generic-assistant.yaml, sales-representative.yaml, customer-support.yaml
  templates_repo.py    template CRUD + versioning + seeding
  resolver.py          template version + overrides -> ResolvedIdentity (deterministic, hashed)
  identities_repo.py   identity CRUD, id allocation, runtime status rows
  selfcontext.py       authoritative self-context + full runtime instructions
  auth.py              API-key RBAC dependency
  api/app.py           FastAPI app factory create_app(settings) ; routers
  voice/               Voicebox client, TTS plugin            (Task 5+)
  stt/                 Moonshine LiveKit STT adapter          (Task 5+)
  runtime/             LiveKit agent worker, supervisor, latency (Task 5+)
  telephony/           Service 014 adapter                    (later)
  expert/              Scoped Service Expert                  (later)
  ui/                  static Workbench (index.html, app.js)  (later)
```

### Data model (SQLite)

- `templates(template_id TEXT PK, created_at TEXT)`
- `template_versions(template_id TEXT, version INTEGER, body_json TEXT, created_at TEXT, PK(template_id, version))` — immutable rows; body is the full Template contract.
- `identities(identity_id TEXT PK, template_id TEXT, template_version INTEGER, overrides_json TEXT, resolved_json TEXT, resolved_hash TEXT, participant_identity TEXT, default_room TEXT, created_at TEXT, updated_at TEXT, FK(template_id, template_version))`
- `runtime_status(identity_id TEXT PK, presence TEXT, status_detail TEXT, pid INTEGER, launched_at TEXT, heartbeat_at TEXT, last_error TEXT)`
- `voice_authorizations(id INTEGER PK, voice_profile_id TEXT, authorized_by TEXT, subject_name TEXT, consent_statement TEXT, source TEXT, created_at TEXT)`
- (later tasks add) `turn_metrics`, `expert_knowledge`, `expert_sessions`.

### Template contract (SOW §7)

```yaml
template_id: sales-representative
version: 1
name: Sales Representative
description: ...
default_self_facts: {role: ..., company: ..., language: en-US}
persona: {objective: ..., style: ..., instructions: ...}
conversation: {greeting: "...{name}...{company}...", barge_in: true, speaking_style: ...}
voice: {default_voice_profile_id: <str|null>, engine: chatterbox-turbo}
required_instance_fields: [name]
allowed_overrides: [name, company, role, language, persona.instructions, greeting, voice.profile_id, conversation.speaking_style, conversation.barge_in]
created_at: <iso8601>
```

Override keys are FLAT dotted strings exactly as listed in `allowed_overrides`. The complete
universe of overridable keys is: `name, company, role, language, persona.instructions,
persona.objective, persona.style, greeting, voice.profile_id, conversation.speaking_style,
conversation.barge_in`. Any key not in the template's `allowed_overrides` → HTTP 422
`override_not_allowed` (details: `field`, `allowed`). Missing `required_instance_fields` → 422
`missing_required_field`.

### Resolved Identity (instance snapshot)

```json
{
  "identity_id": "ava-001",
  "template_id": "sales-representative", "template_version": 1,
  "self": {"identity_id": "ava-001", "name": "Ava", "archetype": "sales-representative",
           "template_version": 1, "company": "Safi", "role": "Account Specialist", "language": "en-US"},
  "persona": {"objective": "...", "style": "...", "instructions": "..."},
  "conversation": {"greeting": "Hello, this is Ava from Safi ...", "barge_in": true, "speaking_style": "..."},
  "voice": {"profile_id": "...", "engine": "chatterbox-turbo"},
  "participant": {"participant_identity": "ava-001", "default_room": "ava-001"},
  "overrides": {"name": "Ava", ...},
  "resolved_hash": "<sha256 of canonical JSON of everything above except resolved_hash>"
}
```

Resolution is a pure function `resolve(template_body, overrides, identity_id) -> ResolvedIdentity`:
template defaults, then overrides, then `{name}`/`{company}`/`{role}` placeholders in `greeting`
and `persona.instructions` are substituted with resolved self-facts. Canonical JSON =
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.

### Participant / presence contract (SOW §14) — returned by GET /identities/{id}/status

```json
{"identity_id": "ava-001", "participant_identity": "ava-001", "default_room": "ava-001",
 "presence": "OFFLINE", "status_detail": null, "last_error": null,
 "capabilities": {"audio_receive": true, "audio_publish": true, "barge_in": true, "video_publish": false},
 "launched_at": null, "heartbeat_at": null}
```
`capabilities.barge_in` mirrors the resolved `conversation.barge_in`. No TTS/STT/LLM/prompt internals in this object (SOW §19).

---

### Task 1: Project scaffold, config, SQLite migrations

Create the uv project and persistence foundation.

- `pyproject.toml`: name `service015`, `requires-python = "==3.12.*"`, src layout, deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `pyyaml`, `httpx`; dev deps: `pytest`, `openapi-spec-validator`, `jsonschema`. Create `.python-version` = `3.12`. Run `uv sync`.
- `.gitignore`: `.env`, `data/`, `.venv/`, `__pycache__/`, `.superpowers/`, `*.db*`, `evidence/raw/`.
- `.env.example` listing (with comments): `SERVICE015_API_KEYS`, `SERVICE015_DB_PATH`, `SERVICE015_HOST`, `SERVICE015_PORT` (default 8015), `SERVICE015_PUBLIC_URL`, `LLM_BASE_URL` (default `https://api.groq.com/openai/v1`), `LLM_API_KEY`, `LLM_MODEL` (default `llama-3.3-70b-versatile`), `LIVEKIT_URL` (default `ws://localhost:7880`), `LIVEKIT_API_KEY` (devkey), `LIVEKIT_API_SECRET` (secret), `VOICEBOX_URL` (default `http://127.0.0.1:17493`).
- `src/service015/config.py`: `Settings(BaseSettings)` reading those vars (env file `.env`), with `api_keys` parsed into `dict[str, Role]`; `get_settings()` cached.
- `src/service015/db.py`: `connect(path) -> sqlite3.Connection` (creates parent dir, `row_factory=sqlite3.Row`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `busy_timeout=5000`), `migrate(conn)` applying `migrations/*.sql` in filename order exactly once (recorded in `schema_migrations(name TEXT PK, applied_at TEXT)`).
- `migrations/001_init.sql`: the five tables in the Data model section above.
- Tests: `tests/test_db.py` — migrate is idempotent (run twice, tables exist once, schema_migrations has 1 row); foreign key enforcement on identities→template_versions; settings parse `SERVICE015_API_KEYS` into roles and reject an unknown role with a clear error.

Done when `uv run pytest -q` passes and the scaffold is committed.

### Task 2: Templates — starter archetypes, versioning, repository

- `src/service015/models.py`: pydantic models `TemplateBody` (the Template contract above; `allowed_overrides` validated ⊆ the override universe; `required_instance_fields` ⊆ allowed_overrides), `TemplateCreate` (same minus `version`, `created_at`), `TemplateSummary` (template_id, latest_version, name, description), `ErrorBody`.
- `src/service015/starter_templates/*.yaml`: three REAL distinct archetypes — **generic-assistant** (helpful general assistant, required [name]), **sales-representative** (consultative discovery-first sales rep: qualify needs, explain value, propose next step, never invent pricing/discounts, required [name, company]), **customer-support** (empathetic troubleshooting: acknowledge, clarify, step-by-step resolution, escalate when unsure, never invent account data, required [name, company]). Each has its own objective, style, base instructions, greeting with `{name}`/`{company}` placeholders, speaking style, `voice.engine: chatterbox-turbo`, `default_voice_profile_id: null`. Instructions must be written for SPOKEN replies (short sentences, no markdown, no lists).
- `src/service015/templates_repo.py`: `seed_starter_templates(conn)` (inserts v1 of each starter only if that template_id does not exist — idempotent, never overwrites), `create_template(conn, TemplateCreate) -> TemplateBody` (new template_id → v1; existing template_id → next version; prior versions untouched), `list_templates(conn) -> list[TemplateSummary]`, `get_template(conn, template_id, version: int|None=None) -> TemplateBody` (None = latest), `list_versions(conn, template_id) -> list[int]`. Raise a `NotFound` domain exception (define `service015/errors.py` with `DomainError(code, message, status, details)` and subclasses `NotFound`, `ValidationFailed`, `Conflict`, `DependencyUnavailable`).
- Tests: three starters seeded, distinct instructions/objectives; seeding twice is a no-op; creating sales-representative again yields v2 and v1 body is byte-identical to before; invalid allowed_overrides key rejected.

### Task 3: Resolver, identities repository, authoritative self-context

- `src/service015/resolver.py`: `validate_overrides(template, overrides)` (rules in "Template contract" section; also type checks: `conversation.barge_in` bool, everything else non-empty str ≤ 4000 chars; `language` matches BCP-47-ish `^[a-z]{2,3}(-[A-Z]{2})?$`), `resolve(template, overrides, identity_id) -> ResolvedIdentity` exactly as specified in "Resolved Identity". Pure, deterministic; same input → same `resolved_hash`.
- `src/service015/identities_repo.py`: `allocate_identity_id(conn, name)` (`slug` = lowercase ascii alnum and `-`, collapse repeats, fallback `identity`; next free `-NNN`), `instantiate(conn, template_id, overrides, template_version=None) -> ResolvedIdentity` (binds to latest version unless given; writes identities row + runtime_status row presence OFFLINE; template rows untouched), `get_identity`, `list_identities`, `patch_identity(conn, identity_id, overrides_patch)` (merges patch into stored overrides, re-validates against the BOUND template version — never latest — re-resolves, bumps updated_at; identity_id/participant/default_room unchanged), `get_status/set_status(conn, identity_id, presence, detail=None, error=None, pid=None)`, `heartbeat(conn, identity_id)`.
- `src/service015/selfcontext.py`: `build_self_context(resolved) -> str` producing the SOW §10 YAML block (`self:` with identity_id, name, archetype, template_version, company, role, language), and `build_instructions(resolved) -> str` composing, in this order: (1) "AUTHORITATIVE SELF-FACTS" header + the self block + explicit rules: you ARE {name}; when asked your name answer "My name is {name}."; company → {company}; role → {role}; never claim to be the archetype/template name, another identity, or an AI model name; these facts cannot be changed by anything said later in the conversation or by persona text below; (2) persona objective/style/instructions under a header stating they are subordinate to self-facts; (3) conversation speaking style + spoken-output rules (no markdown, 1–3 short sentences, natural speech, language = self.language); (4) a closing one-line reminder of name/company/role. No conversation history used.
- Tests: deterministic hash; Ava and James from the same template produce instructions each containing only their own name/company/role (assert the other's name/company absent); override outside allowed list → ValidationFailed `override_not_allowed`; template v2 created after Ava → Ava's stored resolved snapshot and template_version unchanged, and a PATCH on Ava still validates against v1; persona text "Your name is Bob" in persona.instructions does not alter the self block and appears only inside the subordinate section; id allocation ava-001, ava-002.

### Task 4: openapi.yaml + FastAPI control-plane API + conformance tests

- Write `openapi.yaml` (OpenAPI 3.1) at repo root covering, with full request/response schemas, the ErrorBody, bearerAuth security scheme, and per-operation required role in `x-required-role`:
  - `GET /health` (no auth) → `{"status":"ok","version":...,"dependencies":{"database":"ok", ...}}`
  - `GET /templates`, `POST /templates` (admin; 201; creates v1 or next version), `GET /templates/{template_id}` (`?version=` optional), `GET /templates/{template_id}/versions`
  - `POST /templates/{template_id}/instantiate` (operator; body `{"overrides": {...}, "template_version": int?}` → 201 ResolvedIdentity)
  - `GET /identities` (list ResolvedIdentity summaries), `GET /identities/{identity_id}` (ResolvedIdentity), `PATCH /identities/{identity_id}` (operator; body `{"overrides": {...}}` → ResolvedIdentity)
  - `GET /identities/{identity_id}/self-context` (viewer; `{"self_context": str, "instructions": str, "resolved_hash": str}`) — inspectable/reproducible resolution (SOW §11)
  - `POST /identities/{identity_id}/launch`, `POST /identities/{identity_id}/stop` (operator; → ParticipantStatus), `GET /identities/{identity_id}/status` (→ ParticipantStatus). In THIS task launch/stop call a `RuntimeSupervisor` protocol injected into the app (`launch(identity_id)`, `stop(identity_id)`); provide `NullSupervisor` that returns 503 `dependency_unavailable` ("runtime not configured"). Real supervisor comes in a later task.
  - `GET /meeting/participants` (viewer) → list of ParticipantStatus for all identities — the Meeting-Manager-facing roster (SOW §19; metadata only).
- `src/service015/auth.py`: FastAPI dependency `require_role(role)` per Global Constraints.
- `src/service015/api/app.py`: `create_app(settings, supervisor=None) -> FastAPI`; on startup connect+migrate+seed; DomainError → ErrorBody handler; also map FastAPI RequestValidationError to the ErrorBody shape (code `invalid_request`, 422). Serve the static `openapi.yaml` at `GET /openapi.yaml`. Entry point `uv run service015-api` (console script) runs uvicorn on SERVICE015_HOST/PORT.
- Tests (`tests/test_api.py`, `tests/test_openapi_contract.py`) using FastAPI TestClient with a temp DB: openapi.yaml validates (openapi-spec-validator); every (path, method) in openapi.yaml exists in the app routes and vice-versa for non-internal routes; for each exercised operation, response body validates against the spec response schema (resolve `$ref`s with jsonschema + referencing); SOW TEST 1 (3 templates listed), TEST 2 (instantiate Ava; template body/version unchanged), TEST 3 (all customizations stored; invalid override → 422 `override_not_allowed`), restart persistence at the data layer (new app on same DB file returns identical Ava resolved_hash, identity_id, default_room), RBAC (401 without key, 403 viewer POSTing).

## Voice / runtime architecture (Tasks 5+; facts in docs/RESEARCH.md)

```
Workbench UI / Meeting platform ──REST──> service015 API (FastAPI :8015)
                                             │  launch = LiveKit explicit dispatch (agent_name=service015-identity,
                                             │           room=default_room, metadata={"identity_id": ...})
                                             ▼
LiveKit server (:7880) <──── service015 runtime worker (AgentServer, uv venv)
      ▲                         job: load identity from SQLite → build_instructions (fresh, authoritative)
      │                         Moonshine STT plugin → Groq LLM → ChatterboxTTS plugin ─HTTP─┐
 human (browser / SIP via Service 014)                                                       ▼
                                  voice engine sidecar (:17494, runs in Voicebox's venv: torch+chatterbox)
                                    Chatterbox Turbo loaded once, conditionals cached per Voicebox profile,
                                    sentence-chunked streaming s16le 24 kHz PCM
                                             │ reference samples
                                             ▼
                                  Voicebox headless backend (:17493) — profiles, samples, cloning
```

Decisions:
- Voicebox is the voice workbench and source of voice profiles/samples. Its HTTP generation is not streaming, so the low-latency path is our engine sidecar, which loads Chatterbox Turbo in Voicebox's own venv (deps already reconciled there) and pulls reference audio from Voicebox (`GET /profiles/{id}/samples`, `GET /samples/{sample_id}`; longest sample > 5 s).
- Preview uses the SAME sidecar path as the runtime, so "previewed voice == runtime voice" holds by construction.
- Only cloned Voicebox profiles work with Turbo. Attaching a cloned profile to an Identity REQUIRES a `voice_authorizations` row for that profile (else 422 `voice_not_authorized`).
- Seed voices: two synthetic reference voices generated with the OS TTS (Windows SAPI Zira → "Ava Warm", David → "James Calm"), committed under `assets/voices/` with provenance "synthetic OS TTS voice generated by operator" — authorized by construction, no real person's voice.
- One runtime worker process (AgentServer, `LIVEKIT_AGENT_NAME=service015-identity`, `num_idle_processes>=2` prewarmed) serves all identities; each launched Identity = one job in its Default Room accepted with `identity=participant_identity`, `name=display name`.
- Runtime state is written by the job directly into SQLite `runtime_status` (WAL) — single source of truth; API reads it. Heartbeat every 5 s; status reports ERROR/OFFLINE if heartbeat older than 15 s or worker process is gone.
- Env vars added: `VOICE_ENGINE_URL` (default http://127.0.0.1:17494), `VOICE_ENGINE_DEVICE` (cpu|cuda), `CHATTERBOX_VARIANT` (turbo|turbo-nano, default turbo), `MOONSHINE_ARCH` (tiny|small|medium, default small), `LIVEKIT_AGENT_NAME` (default service015-identity), `SERVICE014_MODE` (disabled|livekit_sip|http), `SERVICE014_URL`, `SERVICE014_API_KEY`, `SERVICE014_SIP_TRUNK_ID`.

### Task 5: Local dependency stack (Voicebox, LiveKit dev server, models, seed voices)

Install and run the EXISTING dependencies first (SOW §25.1). Files: `scripts/setup_deps.ps1`, `scripts/setup_deps.sh`, `scripts/run_deps.ps1`, `scripts/run_deps.sh`, `docker-compose.dev.yml` (livekit-server only), `assets/voices/ava_warm.wav`, `assets/voices/james_calm.wav` (+ `.txt` transcripts), `assets/voices/PROVENANCE.md`, `scripts/make_seed_voices.ps1`, `scripts/seed_voicebox_profiles.py`, `docs/DEPENDENCIES.md`.
- Clone Voicebox into `deps/voicebox` (git-ignored) at a pinned commit SHA recorded in `docs/DEPENDENCIES.md` and the scripts. Create its venv with `uv venv --python 3.12 deps/voicebox/.venv`, install per docs/RESEARCH.md (fall back to the minimal chatterbox_turbo dependency set if LuxTTS/piper deps fail on Windows — record exactly what was installed). Torch CPU wheels on dev; scripts accept `-Device cuda` / `DEVICE=cuda` for EC2 (CUDA 12.x torch wheels).
- Run Voicebox headless: `deps/voicebox/.venv` python `-m backend.main --host 127.0.0.1 --port 17493 --data-dir data/voicebox`. Verify `GET /health` and `GET /profiles`.
- Download Chatterbox Turbo weights (via `POST /models/load {"model_name":"chatterbox-turbo"}` or `ChatterboxTurboTTS.from_pretrained`) and record time/size.
- LiveKit dev server via `docker-compose.dev.yml` per docs/RESEARCH.md (single UDP mux 7882, node-ip 127.0.0.1, pinned image tag ≥2 weeks old). Verify with `livekit-api` `list_rooms` from the service015 venv (add `livekit-api==1.2.1` dep to pyproject).
- Generate seed voice clips: `scripts/make_seed_voices.ps1` uses Windows SAPI (System.Speech) to render ~10–12 s of natural text with "Microsoft Zira" → ava_warm.wav and "Microsoft David" → james_calm.wav (mono 16-bit), transcripts in .txt. Also render human test utterances for Task 10 into `assets/test_audio/` with a THIRD voice if available (else David at a different rate) — list in Task 10.
- Create the two Voicebox cloned profiles ("Ava Warm", "James Calm", default_engine chatterbox_turbo, language en) via the Voicebox API in `scripts/seed_voicebox_profiles.py` (service015 venv, httpx; idempotent by name). Print their ids.
- Run one Voicebox Turbo generation for "Ava Warm" through Voicebox's own `/generate` to prove the stack; record wall-time and audio duration (real-time factor) in `docs/DEPENDENCIES.md`.
- No service015 package code in this task. Do NOT commit `deps/` or `data/`.

### Task 6: Voice engine sidecar (Chatterbox Turbo streaming)

`engine/chatterbox_server.py` (+ `engine/README.md`) — a small FastAPI/uvicorn app executed with Voicebox's venv interpreter. Must not import service015.
- Startup: load `ChatterboxTurboTTS.from_pretrained(VOICE_ENGINE_DEVICE as string, nano=(CHATTERBOX_VARIANT=="turbo-nano"))` once; run a warm-up generate; then report ready.
- `GET /health` → `{"status":"ok"|"loading"|"error","engine":"chatterbox-turbo","variant":...,"device":...,"sample_rate":24000,"warm":bool,"cached_profiles":[...]}`.
- `POST /warm {"profile_id"}` → fetch reference audio from Voicebox (`VOICEBOX_URL`), choose longest sample > 5 s (422 `reference_too_short` otherwise), `prepare_conditionals`, cache conditionals per profile_id (LRU 8), run one tiny generate. Return timings.
- `POST /synthesize/stream {"profile_id","text"}` → `StreamingResponse` `audio/pcm` (s16le mono 24 kHz) with header `X-Sample-Rate: 24000`; split text into sentences/clauses (first chunk as short as a natural clause), generate per chunk, yield each chunk's PCM as soon as it is ready; generation in a single worker thread guarded by a lock; client disconnect cancels remaining chunks (barge-in must stop work). Unknown profile → 404; Voicebox unreachable → 503 `voicebox_unavailable`.
- `POST /preview {"profile_id","text"}` → full `audio/wav` (same synthesis path).
- Log per-request timings (first-chunk latency, RTF) — never log sample audio.
- `scripts/run_engine.ps1|.sh` start it on port 17494. A pytest under `tests/engine/` skipped unless `VOICE_ENGINE_URL` reachable, exercising health/warm/stream (first chunk arrives before full synthesis for 2-sentence text) against the real engine; record results in `docs/DEPENDENCIES.md` (first-chunk latency, RTF on this CPU, both variants turbo and turbo-nano).

### Task 7: Voice integration in the service (Voicebox client, voices API, authorization)

- `src/service015/voice/voicebox.py`: async `VoiceboxClient` (httpx) — `list_profiles()`, `get_profile(id)`, `create_cloned_profile(name, language, description, sample_bytes, filename, reference_text)`, `health()`. Errors → `DependencyUnavailable("voicebox_unavailable")`.
- `src/service015/voice/engine.py`: async `VoiceEngineClient` — `health()`, `warm(profile_id)`, `preview(profile_id, text) -> bytes`, `stream(profile_id, text)` async iterator of PCM bytes.
- API (openapi + conformance): `GET /voices` (viewer) → cloned profiles: `voice_profile_id, name, language, engine:"chatterbox-turbo", authorized: bool`; `POST /voices` (admin, multipart: name, language, description, reference_text, file, consent_statement, subject_name, authorized_by) → creates the Voicebox cloned profile then the `voice_authorizations` row (if Voicebox fails, no row) → 201; `POST /voices/{voice_profile_id}/authorizations` (admin, json consent_statement, subject_name, authorized_by, source) for profiles created directly in Voicebox; `POST /voices/{voice_profile_id}/preview` (operator, json `{"text"?: str}`, default "Hello, this is a preview of my voice.") → `audio/wav`; `GET /health` includes `voicebox` and `voice_engine` dependency states (never failing /health itself).
- Instantiate/PATCH: non-null resolved `voice.profile_id` must exist in Voicebox AND have an authorization row → else 422 `voice_profile_not_found` / `voice_not_authorized`; Voicebox down → 503 `voicebox_unavailable`, nothing persisted.
- Never return or log raw sample audio.
- Tests with `respx` (dev dep) mocking Voicebox/engine; integration test (skipped unless real Voicebox reachable) listing the seeded profiles.

### Task 8: LiveKit plugins — Moonshine STT adapter and Chatterbox TTS plugin

- Deps: `livekit-agents==1.8.3`, `livekit-plugins-openai==1.8.3`, `livekit-plugins-silero==1.8.3`, `livekit==1.1.20`, `moonshine-voice==0.1.5`, `numpy`.
- `src/service015/stt/moonshine.py`: `MoonshineSTT(stt.STT)` + `MoonshineStream(stt.RecognizeStream)` per docs/RESEARCH.md: one Moonshine `Transcriber` per process (created in prewarm, injected), one Moonshine stream per RecognizeStream; feeding in a dedicated thread (never block the loop); int16 → float32 [-1,1] at 16 kHz; START_OF_SPEECH on first non-empty line, INTERIM_TRANSCRIPT on updates, FINAL_TRANSCRIPT on `LineCompleted` (and on flush with pending text), END_OF_SPEECH after final. Errors surface as the framework's API error types (verify in 1.8.3). Verify moonshine_voice import paths in the installed package first.
- `src/service015/voice/livekit_tts.py`: `ChatterboxTTS(tts.TTS)` (streaming=False, 24 kHz mono) whose ChunkedStream POSTs to the sidecar `/synthesize/stream` and pushes PCM to the emitter as it arrives; closes the HTTP stream promptly on cancellation; optional `on_first_audio(t_monotonic)` callback (T3); connection errors → framework APIConnectionError.
- Tests: STT test feeding a real wav (`assets/test_audio/what_is_your_name.wav`) through MoonshineStream asserting a FINAL_TRANSCRIPT containing "name"; TTS plugin test with a fake streaming server asserting incremental push + first-audio callback; integration test (skip unless engine reachable) via the real sidecar.

### Task 9: Runtime worker, launch/stop/status supervisor, latency recording

- `migrations/002_runtime.sql`: `turn_metrics(id INTEGER PK, identity_id, session_id, turn_index, t0..t5 REAL NULL (monotonic seconds), user_text, agent_text, interrupted INTEGER, created_at)`, `runtime_sessions(session_id PK, identity_id, room, started_at, ended_at, end_reason)`.
- `src/service015/runtime/worker.py` (console script `service015-runtime`): `AgentServer` with `setup_fnc` prewarming silero VAD + Moonshine Transcriber; `on_request` loads identity from `metadata.identity_id`, rejects if missing, accepts with `identity=participant_identity`, `name=self.name`. Entrypoint: STARTING → connect → warm engine for the voice profile → `Agent(instructions=build_instructions(resolved))` built FRESH from the DB per job → `AgentSession(stt=MoonshineSTT, vad, llm=openai.LLM(base_url/api_key/model from env), tts=ChatterboxTTS(profile), turn_handling={interruption:{enabled: barge_in, mode:"vad", min_duration:0.3}, endpointing:{min_delay:0.3}, preemptive_generation: True})` → `session.say(greeting)` → READY (BUSY while a non-agent participant is present). Heartbeat every 5 s. LiveKit disconnect → ERROR detail RECONNECTING; reconnect → READY. LLM/STT/TTS errors → ERROR with `status_detail` `llm_unavailable` / `stt_failure` / `tts_unavailable`, never fabricated speech. Job end → OFFLINE.
- `src/service015/runtime/latency.py`: per-turn T0 (user_state_changed speaking→listening), T1 (final user_input_transcribed), T2 (first LLM chunk — wrap `Agent.llm_node`), T3 (TTS first-PCM callback), T4 (agent_state_changed → speaking); T5 filled by the e2e harness. `report(conn, identity_id, session_id=None)` → `{turns, t0_t4:{p50,p95,max}, t0_t1, t1_t2, t2_t3, t3_t4, t4_t5}` in ms.
- `src/service015/runtime/supervisor.py`: `LiveKitSupervisor(settings)` implementing the Task-4 protocol: `ensure_worker()` (spawn `service015-runtime` subprocess if not alive; bounded 60 s wait else 503 `runtime_unavailable`); `launch(identity_id)`: preflight (LiveKit reachable, engine healthy + profile warmable, LLM `GET /models`) → 503 `dependency_unavailable` with `details.dependency`, identity untouched; ensure room `default_room`; create dispatch with metadata; STARTING; idempotent when already STARTING/READY/BUSY. `stop(identity_id)`: delete room dispatches + remove participant; OFFLINE. Status staleness rules.
- API (openapi + conformance): `POST /identities/{id}/join-token` (operator; `{"participant_name"?}` → `{"livekit_url","room","token","participant_identity"}`, identity `human-<random8>`), `GET /identities/{id}/latency` (viewer). Wire `LiveKitSupervisor` in `create_app` when LiveKit settings present.
- Tests: latency percentile unit tests; preflight failure leaves identity untouched (mocks); integration (skip unless LiveKit+engine+LLM key): launch Ava → READY ≤ 60 s and participant `ava-001` in room `ava-001`; stop → OFFLINE; relaunch → same identity/room.

### Task 10: End-to-end voice acceptance harness (real audio, real room)

`tests/e2e/` + `scripts/run_acceptance.py` — an automated HUMAN participant (livekit rtc) joins an Identity's Default Room with a join-token from the API, publishes pre-rendered speech wavs (`assets/test_audio/`, a voice distinct from the identity voices — never synthesized by the system under test), subscribes to the Identity's audio, transcribes it with Moonshine.
- Scenarios (JSON evidence to `evidence/<timestamp>/`):
  1. Self-knowledge (TEST 4/5): "What is your name?", "Who do you work for?", "What is your role?" → configured values.
  2. Isolation (TEST 6): James from the same template with different name/company/role/voice → James's facts only; no Ava facts, and vice versa.
  3. Ten-turn conversation (TEST 9): ≥10 coherent turns from a sales-relevant question list; each reply non-empty and relevant.
  4. Barge-in (TEST 10): request a long answer, start speaking 1.5 s into the Identity's speech; time from human speech onset to Identity audio silent (RMS threshold) — PASS ≤ 1,000 ms — and the next reply addresses the interruption.
  5. Latency (TEST 11): ≥10 turns → `GET /identities/{id}/latency` + harness-measured T5 → `evidence/<ts>/latency.json` + `latency.md` (P50/P95/MAX for T0→T4 and each interval).
  6. Restart persistence (TEST 13): stop API+runtime, restart, relaunch Ava → same identity_id/template_version/name/persona/voice/default_room, still answers "Ava".
  7. Default room (TEST 8): consecutive launches use the same room + participant identity.
  8. Failure/recovery (SOW §27): engine stopped → launch 503 dependency failure, identity unchanged, engine restored → relaunch OK; invalid override → 422; LLM unreachable → presence ERROR `llm_unavailable`, no fabricated speech.
- `scripts/run_acceptance.py` prints a PASS/FAIL table; `evidence/` committed (raw audio under `evidence/raw/` git-ignored).

### Task 11: Service 014 telephony adapter (no SIP implementation)

- `src/service015/telephony/service014.py`: `Service014Client` with `connect_phone(identity, to_number)` and `inbound_route(identity)`; backend by `SERVICE014_MODE`: `disabled` (503 `telephony_not_configured`), `http` (POST `{SERVICE014_URL}/calls` json `{room, to}` bearer `SERVICE014_API_KEY` — assumed contract, documented as such), `livekit_sip` (Service 014's SIP primitive on the same LiveKit deployment: `create_sip_participant(sip_trunk_id=SERVICE014_SIP_TRUNK_ID, sip_call_to=to, room_name=default_room, participant_identity="phone-<last4>", wait_until_answered=True)`; inbound documented as `SIPDispatchRuleDirect(room_name=default_room)`).
- API: `POST /identities/{id}/phone/dial` (operator; `{"to": E.164}`) → 202 `{call_id, room, status}`; 409 unless presence READY/BUSY; `GET /identities/{id}/phone` (viewer) → `{mode, default_room, inbound}`.
- `docs/PHONE_DOGFOOD.md`: exact TEST 12 procedure with real Service 014 + Exotel. TEST 12 = BLOCKED (external) until real endpoints/trunk exist.
- Mocked tests for both backends, E.164 validation, 503 when disabled.

### Task 12: Scoped Service Expert (Service 013 stand-in) + fresh-learning

- `migrations/003_expert.sql`: `expert_knowledge(id PK, kind CHECK IN ('document','verified_experience'), title, body, source, repo, branch, sha, min_role DEFAULT 'viewer', created_at)`, `expert_sessions(session_id PK, role, created_at)`, `expert_messages(id PK, session_id, role, content, created_at)`.
- `expert/manifest.yaml`: service 015 scope; knowledge sources (`docs/SOW.md` = SOW text, openapi.yaml, starter templates, identity schema doc, docs/RESEARCH.md, docs/DEPENDENCIES.md, README, latency evidence, PHONE_DOGFOOD); RBAC context permissions (viewer: public docs; admin-only: voice authorization provenance; raw samples never); repo/branch/SHA resolved by `git rev-parse` at ingest.
- `src/service015/expert/`: `ingest_manifest()` (~800-char chunks with overlap, provenance per chunk; re-ingest replaces `document` chunks of the same source, NEVER deletes `verified_experience`), in-repo BM25, `ask(conn, session_id, question, role)` → role-filtered top-k → configured LLM with grounded-answer system prompt, citations `[source@sha]`, verified experience preferred → `{answer, citations}`.
- API: `POST /expert/experience` (admin) → 201; `GET /expert/knowledge` (viewer, metadata only); `POST /expert/sessions` (viewer) → `{session_id}`; `POST /expert/sessions/{sid}/ask` (viewer). CLI `service015-expert ingest|ask`.
- Fresh-learning (TEST 15): ingest a real verified fact from Tasks 5–10 (planner supplies it), NEW session, question requiring it → answer contains it and cites the verified_experience; evidence `evidence/expert_fresh_learning.json`. Unit tests (BM25, RBAC) with LLM mocked; real run against Groq.

### Task 13: Minimal Workbench UI + Talk page

- `src/service015/ui/` static (plain HTML+JS, no build) served at `/`: API key field (localStorage); SOW §17 form (Template, Name, Company, Role, Language, Voice + [Preview], Greeting, Persona, Speaking style, Barge-in); buttons [CREATE IDENTITY] / [SAVE] (PATCH) / [LAUNCH] / [STOP] / [TEST]; identity list with presence (refresh after actions and every 5 s only while visible); fields outside the template's allowed_overrides disabled; ErrorBody shown verbatim.
- `talk.html?identity=<id>`: join-token → `livekit-client` UMD from jsdelivr pinned version ≥2 weeks old → publish mic, play Identity audio, show presence/transcript, [Hang up].
- Expert tab (fresh session ask).
- Everything via the public API. Test: `/` and `/talk.html` served; browser check with screenshots in evidence.

### Task 14: Packaging, EC2 deployment, docs, integration guide

- `Dockerfile` (service015 API+runtime), `engine/Dockerfile` (Voicebox backend + sidecar; `DEVICE=cpu|cuda`, CUDA variant on an nvidia/cuda 12.x runtime base), `docker-compose.yml` (livekit-server + redis, voicebox+engine, service015; `sip` profile adding `livekit/sip` for Service 014; GPU reservation), `deploy/EC2.md` (g4dn.xlarge/g5.xlarge, security-group ports, TLS via Caddy for https/wss, env, LiveKit Cloud alternative).
- `README.md` exact install/run/test commands (Windows + Linux); `docs/INTEGRATION.md` for the Meeting Manager platform (auth/roles, curl walkthrough: roster, instantiate, launch, presence semantics, join-token, phone dial; what the platform does NOT need to know); `docs/ACCEPTANCE.md` (TEST 1–15 + §27 matrix with status + evidence links).
- Secret scan; `.env.example` complete.
