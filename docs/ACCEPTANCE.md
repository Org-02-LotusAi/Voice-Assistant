# Acceptance: SOW section 26 (TEST 1-15) and section 27 (failure and recovery)

How to read this file. Each row names the **mechanism** that proves the test (an automated test, a script, or a manual
step) and its **status** as of 2026-10-10. Status vocabulary:

- **PASS (automated)**: proven by tests that run offline in `uv run pytest` (see "Reproduce" below); last full run of the
  offline suite: recorded in the Task 14 report (`--ignore=tests/test_runtime_live.py --ignore=tests/e2e`).
- **PASS (live)**: proven by a live end-to-end scenario run by `scripts/run_acceptance.py` against the real stack
  (LiveKit + Voicebox + engine + Groq + runtime worker + a scripted human speaking pre-rendered wav files). Evidence is
  under `evidence/<UTC timestamp>/`. The accepted run is `20261010T153557Z` (code SHA `c806c9e`, see Release).
- **BLOCKED (external)**: cannot be executed without things outside this repo.

Accepted run: `evidence/20261010T153557Z/` (full run, `--allow-latency-fail`, `SERVICE015_RUNTIME_LOG=DEBUG`, 2026-10-10, code `c806c9e`): self_knowledge PASS, isolation PASS, ten_turn PASS, latency FAIL-ON-THIS-HOST, barge_in PASS, default_room PASS, restart PASS, failure PASS. See `evidence/20261010T153557Z/summary.md`.

## Latency statement (TEST 11)

The SOW ceiling is P95 T0->T4 <= 1,500 ms (target P50 <= 700 ms) on the reference setup. On the CPU-only development
host (Ryzen 5 5500U, 15 GB, no GPU) it cannot be met, and the **measured bottleneck is TTS first-audio (T2->T3)**:
Chatterbox Turbo runs at a real-time factor of about 6-9 on this CPU (first PCM of a ~40 character first clause after
13-20 s; `docs/DEPENDENCIES.md`), versus about 0.5-1.5 s for STT final (T0->T1, measured) and about 0.3 s for the
first LLM chunk (T1->T2, Groq `qwen/qwen3.8-27b`, `reasoning_effort: none`, TTFT about 1 s measured by the controller).
Avoidable cold starts are removed: models load once per worker/job process, the engine warms the voice conditionals at job
start and before the greeting, and the model is resident between turns. The GPU target (g4dn.xlarge T4 / g5.xlarge A10G)
is **pending**: it is exercised by `uv run python scripts/run_acceptance.py --only latency` on the GPU host
(`deploy/EC2.md`), and TEST 11 is only fully PASS once that run exists. Do not pass `--allow-latency-fail` on the GPU host.

## TEST 1-15

| # | Test (SOW 26) | Mechanism | Status | Evidence |
|---|---|---|---|---|
| 1 | Template Workbench: >= 3 distinct archetypes selectable | `tests/test_api.py::test_test1_three_templates_listed`, `tests/test_templates.py::test_three_starters_seeded_and_distinct`; UI served and lists templates: `tests/test_ui.py` and manual: open `http://localhost:8015/`, the Template picker shows Generic Assistant, Sales Representative, Customer Support | PASS (automated) | UI screenshots `evidence/ui/workbench-locked-template-disabled-fields.jpg`, `evidence/ui/workbench-identity-created-save-error.jpg` |
| 2 | Instantiate Ava without mutating the Template | `tests/test_api.py::test_test2_instantiate_ava_template_unchanged`; `tests/test_templates.py::test_new_version_leaves_v1_byte_identical`; `tests/test_identities.py::test_template_v2_does_not_touch_ava_and_patch_uses_bound_version` | PASS (automated) | pytest output |
| 3 | Customization within allowed overrides; arbitrary fields rejected | `tests/test_api.py::test_test3_customizations_stored_and_invalid_override_rejected` (name, company, role, greeting, persona, language, voice stored; unknown key -> 422 `override_not_allowed`); `tests/test_identities.py` (type/limit/injection checks) | PASS (automated) | pytest output |
| 4 | Authoritative name in a fresh runtime session | Unit: `tests/test_identities.py::test_persona_text_cannot_alter_self_block`, self-context builder tests. Live: scenario `self_knowledge` in `scripts/run_acceptance.py` (spoken "What is your name?" -> Moonshine -> Groq -> Chatterbox, fresh session per run) | PASS (unit) / PASS (live, 20261010T153557Z) | `evidence/20261010T153557Z/1-self_knowledge.json` (name: LLM text and audio transcript both "Ava") |
| 5 | Authoritative company and role | Unit: self-context tests above. Live: scenario `self_knowledge` (company and role questions) | PASS (unit) / PASS (live, 20261010T153557Z); audio transcript heard "Saffy" for "Safi" (STT caveat), LLM text "I work for Safi." | `evidence/20261010T153557Z/1-self_knowledge.json` |
| 6 | Same Template, different Identity (no leakage) | Unit: `tests/test_identities.py::test_ava_and_james_self_facts_never_mix`. Live: scenario `isolation` (Ava and James from the same template, each asked their own facts) | PASS (unit) / PASS (live, 20261010T153557Z; James said Northwind/Solutions Consultant, never Ava's facts, and vice versa) | `evidence/20261010T153557Z/2-isolation.json` |
| 7 | Selected Voicebox profile is previewed and used by the launched Identity (no silent default voice) | `tests/test_voices.py` (preview via the engine path, authorization gate, `test_launch_requires_a_voice`: a voiceless Identity refuses to launch with 422 `voice_required`, never a fallback voice); `tests/test_runtime_lifecycle.py::test_entrypoint_refuses_voiceless_identity_then_finish_keeps_error`. Same sidecar serves preview and runtime, so previewed voice == runtime voice by construction. Live: scenarios `self_knowledge`/`ten_turn` speak with the profile's conditionals | PASS (automated) / PASS (live, 20261010T153557Z: Ava and James spoke with different profiles) | `evidence/20261010T153557Z/2-isolation.json` |
| 8 | Launched Identity appears as the configured participant in its persistent Default Room | `tests/test_runtime.py` (launch dispatch, `participant_identity == default_room == identity_id`). Live: scenario `default_room` (list_participants shows `ava-001`, name Ava, kind AGENT, relaunch reuses the same room and identity) | PASS (unit) / PASS (live, 20261010T153557Z: 3 launches, room and identity `ava-001` each time) | `evidence/20261010T153557Z/6-default_room.json` |
| 9 | Natural conversation, >= 10 coherent voice turns | Live only: scenario `ten_turn` (scripted human audio, real STT/LLM/TTS, no scripted playback of agent speech) | PASS (live, 20261010T153557Z: 10/10 turns; also 2026-10-10 `20261010T104732Z`, `20261010T115054Z`) | `evidence/20261010T153557Z/3-ten_turn.json` |
| 10 | Barge-in: AI stops promptly, listens, responds to the interruption | Live only: scenario `barge_in` (human speaks over the agent; asserts the agent stops and answers the interruption). Config unit: `tests/test_api.py::test_barge_in_mirrored_in_status` | PASS (live, 20261010T153557Z: agent audio silent 609 ms after speech onset, limit 1000 ms; earlier runs 313-375 ms, `20261010T103805Z`..`20261010T114448Z`, 6/6) | `evidence/20261010T153557Z/5-barge_in.json` |
| 11 | Latency evidence: P50/P95/MAX and components over >= 10 turns | Live: scenario `latency` (T0..T4 per turn, `GET /identities/{id}/latency`, writes `latency.md` + `evidence/latency*.json`). Recorder unit tests: `tests/test_runtime.py` (percentiles, latching, T5). See "Latency statement" above | **FAIL-ON-THIS-HOST** (CPU): 13 turns, T0->T4 P50 18,188 ms, P95 20,750 ms, MAX 20,750 ms; bottleneck T2->T3 (first LLM chunk -> first TTS PCM) P50 16,562 ms, versus T0->T1 1,000 ms, T1->T2 297 ms, T3->T4 0 ms. All other latency checks (>= 10 turns, every interval captured) pass. GPU target PENDING (run on the EC2 host) | `evidence/20261010T153557Z/latency.md`, `evidence/latency.md`, `evidence/latency.json`; GPU: not yet run |
| 12 | Phone dogfood via real Service 014 + Exotel | Manual: `docs/PHONE_DOGFOOD.md` (exact runbook). Mocked adapter tests: `tests/test_telephony.py` | **BLOCKED (external)**: needs a real Service 014 deployment, an Exotel number and a LiveKit SIP trunk | n/a until run |
| 13 | Restart Service 015, relaunch Ava, same identity | Unit: `tests/test_api.py::test_restart_persistence`, `tests/test_identities.py::test_persistence_across_connections`. Live: scenario `restart` (stop the API process, start a new one on the same DB, relaunch Ava, compare identity_id/template version/name/persona/voice/default_room) | PASS (unit) / PASS (live, 20261010T153557Z: stop API, new process on the same DB, relaunch, snapshot and resolved_hash identical, still answers "Ava") | `evidence/20261010T153557Z/7-restart_persistence.json` |
| 14 | OpenAPI contract: every operation validates and conforms | `tests/test_openapi_contract.py` (spec validates with openapi-spec-validator; every spec operation exists in the app and vice versa; responses validate against the spec schemas; role annotations match); both copies of `openapi.yaml` kept byte-identical (`cmp openapi.yaml src/service015/openapi.yaml`) | PASS (automated) | pytest output |
| 15 | Fresh expert learning: retrieve a newly verified fact in a fresh session | Offline: `tests/test_expert.py` (ingest, provenance, RBAC, restart persistence, mocked LLM). Live: `uv run python scripts/expert_fresh_learning.py --title ... --body ... --question ... --expect ...` (control ask before ingest, ingest, new app instance + new session, PASS needs the expected text and a citation of the experience row); `tests/test_expert_live.py` with `LLM_API_KEY` | PASS (offline) / **PASS (live, 2026-10-10, sha 6faa313, Groq `qwen/qwen3.8-27b`)**: the fact was absent before ingest, retrieved at rank 1 and cited (verified_experience #235) in a fresh session after ingest | `evidence/expert_fresh_learning.md`, `evidence/expert_fresh_learning.json` |

## Section 27: failure and recovery

| Scenario | Mechanism | Status | Evidence |
|---|---|---|---|
| Voice service unavailable: explicit dependency failure on launch/status, Identity not corrupted, relaunch without recreating | Unit: `tests/test_voices.py::test_launch_rechecks_voice_existence_authorization_and_voicebox` (503 `voicebox_unavailable`, Identity row untouched), `test_instantiate_voicebox_down_is_503_and_persists_nothing`; classification `tts_unavailable` in `tests/test_runtime_lifecycle.py`. Live: scenario `failure` stops and restarts the voice engine | PASS (unit) / PASS (live, 20261010T153557Z: launch with the engine down -> 503 `dependency_unavailable` naming `voice_engine`, Identity unchanged, engine restored warm in 133.5 s, relaunch READY) | `evidence/20261010T153557Z/8-failure_recovery.json` |
| LiveKit disconnect: ERROR/RECONNECTING, rejoin same room and identity | Unit: `tests/test_runtime_lifecycle.py::test_room_disconnect_ends_as_error_livekit_disconnected_not_offline`, `test_reconnecting_maps_to_error_and_back`, `test_relaunch_after_livekit_disconnect_rejoins_same_room_and_identity`. Not exercised live (stopping LiveKit would disturb other runs) | PASS (unit); live NOT RUN | pytest output |
| LLM endpoint unavailable: no fabricated answers, error state surfaced, Identity preserved | Unit: `tests/test_runtime_lifecycle.py` (error classification, recoverable vs latched, scoped recovery); `tests/test_expert.py` (503 `llm_unavailable`, nothing stored). Live: scenario `failure` part (b) points the LLM at a broken endpoint and expects presence ERROR `llm_unavailable` with no speech | PASS (unit) / PASS (live, 20261010T153557Z: ERROR `llm_unavailable`, 0.00 s of audio) | `evidence/20261010T153557Z/8-failure_recovery.json` |
| STT failure: reported without replacing self-facts | Unit: `tests/test_runtime_lifecycle.py::test_classify_error_names_the_real_component` (`stt_failure`) and the latch/recovery tests; self-facts come from SQLite, not conversation state | PASS (unit) | pytest output |
| Runtime process crash: restart and relaunch the same Identity from persisted state | Unit: `tests/test_runtime_lifecycle.py` (pidfile adoption, `reconcile_runtime_lost`, staleness to `ERROR heartbeat_lost`/`worker_exited`, relaunch of a stale Identity). Live: scenario `restart` (kill the API process; the worker tree dies with it) | PASS (unit) / PASS (live, 20261010T153557Z: restart persistence, stale runtime reconciled to OFFLINE, relaunch BUSY) | `evidence/20261010T153557Z/7-restart_persistence.json` |
| Invalid override: rejected with a clear OpenAPI error | `tests/test_api.py::test_test3_...` and `tests/test_identities.py::test_override_not_allowed` (422 `override_not_allowed`, body validated against the spec). Live: scenario `failure` part (a) (disallowed key, non-boolean barge_in, empty name, missing required field -> 422, Identity unchanged) | PASS (automated) / PASS (live, 20261010T153557Z: four invalid overrides -> 422, nothing created) | pytest output; `evidence/20261010T153557Z/8-failure_recovery.json` |

## Safi STT caveat (TEST 4/5/6 audio path)

The scenarios speak the question with pre-rendered human wav files and check the reply twice: the LLM text and a transcript of
the agent's synthesized audio. The transcript comes from the harness's own Moonshine STT, which mis-hears the brand name
"Safi" in a minority of runs (`Saffy` in `20261010T153557Z`, `Sethie` in `20261010T091813Z`, `Safiq` once in the probe), while the
LLM text is correct every time. `evidence/probe/audio_path_probe.json` replays the same sentences through Chatterbox -> STT:
most repeats hear "Safi", so the evidence points at the test STT rather than at TTS mispronunciation, but a TTS-side accent
issue is **not excluded**; a human listen-through on the GPU host should confirm. The harness therefore treats a name
mismatch in the audio transcript as PASS-AUDIO-CAVEAT only when the LLM text is correct; Ava and James (names) and Northwind are
heard correctly.

## Phase 2: accounts, ownership, admin console

| Check | Evidence | Result |
|---|---|---|
| Live smoke on the real DB (API on :8015, real Voicebox, engine, LiveKit): admin creates `demo`; demo forced password change; persona from shared template + shared voice; launch to READY; stop; archive; viewer roster; viewer token GET 200 / POST 403 | `evidence/phase2-smoke/smoke.json`, `evidence/phase2-smoke/README.md` | PASS (statuses only, no secrets) |
| Admin console, every tab (users, tokens with show-once secret and revoke confirmation, templates new version, voices with consent, personas with owner hand-over, Service Expert re-ingest and add experience, live roster) | `evidence/ui-phase2/admin-*.jpg` (run against a throwaway database and API on :8017) | PASS |
| Forced password change, archive confirmation, templates with server guardrails, voice upload, viewer monitor, 429 login message, session Talk page | `evidence/ui-phase2/01-..05-*.jpg`, `login-429.jpg`, `talk-session.jpg` | PASS (mic connect not exercised) |
| Offline suite incl. ownership, throttling, CSRF, admin APIs, UI behavior, openapi conformance | `uv run pytest -q --ignore=tests/test_runtime_live.py --ignore=tests/e2e --ignore=tests/test_expert_live.py` and `tests/e2e/test_expectations.py` | PASS (see the P4 report for counts) |

## Evidence index

| Evidence | What it proves | Result |
|---|---|---|
| `evidence/20261010T153557Z/` (`summary.md`, `1-..8-*.json`, `latency.*`) | Accepted full run at `c806c9e`: TEST 4/5/6/8/9/10/11/13 and SOW 27 | 7 PASS, latency FAIL-ON-THIS-HOST |
| `evidence/latency.json`, `evidence/latency.md` | Copy of the accepted run's latency report (the Service Expert manifest source `latency-evidence`) | FAIL-ON-THIS-HOST, bottleneck T2->T3 |
| `evidence/expert_fresh_learning.{json,md}` | TEST 15 fresh-learning, live Groq, sha `6faa313` | PASS (rank 1, cited) |
| `evidence/probe/audio_path_probe.json` | Chatterbox -> STT replay behind the "Safi" caveat | STT mis-hear in some repeats |
| `evidence/ui/*.jpg` | Workbench and Talk page screenshots (TEST 1) | n/a |
| `evidence/20261010T103805Z` .. `20261010T114448Z` | Six earlier barge-in runs (stop 313-375 ms) | 6/6 PASS |
| `evidence/20261010T104732Z`, `20261010T115054Z` | Earlier ten-turn runs (10/10 each) | PASS |
| `evidence/20261010T095714Z` | Earlier default_room run (3 launches, same room) | PASS |
| `evidence/20261010T053838Z`, `092450Z`, `094807Z`, `091813Z`, `095815Z` | Earlier diagnostic and pre-fix runs, kept for history; superseded by `20261010T153557Z` | superseded |

Latency components (P50 / P95, ms, 13 turns, accepted run): T0->T1 1,000 / 2,187; T1->T2 297 / 969; T2->T3 16,562 / 19,343;
T3->T4 0 / 78; T0->T4 18,188 / 20,750; T4->T5 188 / 344. Fresh-run health: engine warm at the end, no leftover processes.

## TEST 12 and TEST 11 summary

- **TEST 11: FAIL-ON-THIS-HOST.** SOW ceiling (P50 <= 700 ms, P95 <= 1,500 ms) is missed by about 26x on this Ryzen 5 5500U
  CPU. Measured bottleneck: T2->T3 (first LLM chunk to first TTS audio) at 16.6 s P50; STT and LLM together are about 1.3 s.
  GPU target (g4dn.xlarge / g5.xlarge) pending: `deploy/EC2.md` section 6.
- **TEST 12: BLOCKED (external).** Needs a real Service 014 deployment, an Exotel number and a LiveKit SIP trunk
  (`docs/PHONE_DOGFOOD.md`).

## Release

Code SHA the accepted evidence was produced at: `c806c9ead216aef1c8ed0824e296ebe55c405428` (`c806c9e`). Commits after it change
only evidence, docs, `docker-compose.yml` environment forwarding and `deploy/EC2.md`.
Post-evidence commits (after `c806c9e`):
- `33ecb1b` fix(runtime): session-recreation failures are bounded (same backoff) and end the job with ERROR; ERROR stays latched until the LLM produces a reply; Retry-After accepts seconds, HTTP-date, `850ms`/`1.2s`; e2e stack archives `runtime-worker.rotating.log`; compose forwards the recovery and log-rotation variables.

These touch only the session-recovery error path (unit-tested in `tests/test_post_evidence.py`) and config/docs/test-harness archiving. They do not touch turn-taking, STT coalescing, TTS or any path exercised by the acceptance run.


## Reproduce

```bash
# Offline suite (contract, units, mocked integrations): the PASS (automated) rows
uv run pytest tests -q --ignore=tests/test_runtime_live.py --ignore=tests/e2e

# Real-stack voice scenarios: the live rows. Needs LiveKit, Voicebox, the engine and LLM_API_KEY up.
uv run python scripts/run_acceptance.py --allow-latency-fail      # CPU dev host
uv run python scripts/run_acceptance.py                           # GPU reference host (latency ceiling enforced)

# TEST 15 live
uv run python scripts/expert_fresh_learning.py --help
```

`tests/test_runtime_live.py` is a single heavy live scenario that competes with the acceptance run for the same
LiveKit/engine; run one or the other, not both at once.
