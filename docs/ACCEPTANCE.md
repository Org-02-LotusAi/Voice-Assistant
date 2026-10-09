# Acceptance: SOW section 26 (TEST 1-15) and section 27 (failure and recovery)

How to read this file. Each row names the **mechanism** that proves the test (an automated test, a script, or a manual
step) and its **status** as of 2026-10-10. Status vocabulary:

- **PASS (automated)**: proven by tests that run offline in `uv run pytest` (see "Reproduce" below); last full run of the
  offline suite: `392 passed, 6 skipped` (Task 14, `--ignore=tests/test_runtime_live.py --ignore=tests/e2e`).
- **PENDING (Task 10 run)**: proven by a live end-to-end scenario run by `scripts/run_acceptance.py` against the real
  stack (LiveKit + Voicebox + engine + Groq + runtime worker + a scripted human speaking pre-rendered wav files).
  Evidence is written under `evidence/<UTC timestamp>/`; the links are filled in after that run.
- **BLOCKED (external)**: cannot be executed without things outside this repo.

Evidence link convention: `TBD (Task 10 run)` is replaced with a relative path such as `evidence/2026-10-10T.../self_knowledge.json`.

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
| 4 | Authoritative name in a fresh runtime session | Unit: `tests/test_identities.py::test_persona_text_cannot_alter_self_block`, self-context builder tests. Live: scenario `self_knowledge` in `scripts/run_acceptance.py` (spoken "What is your name?" -> Moonshine -> Groq -> Chatterbox, fresh session per run) | PASS (unit) / PENDING (Task 10 run) for the voice scenario | TBD (Task 10 run) |
| 5 | Authoritative company and role | Unit: self-context tests above. Live: scenario `self_knowledge` (company and role questions) | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| 6 | Same Template, different Identity (no leakage) | Unit: `tests/test_identities.py::test_ava_and_james_self_facts_never_mix`. Live: scenario `isolation` (Ava and James from the same template, each asked their own facts) | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| 7 | Selected Voicebox profile is previewed and used by the launched Identity (no silent default voice) | `tests/test_voices.py` (preview via the engine path, authorization gate, `test_launch_requires_a_voice`: a voiceless Identity refuses to launch with 422 `voice_required`, never a fallback voice); `tests/test_runtime_lifecycle.py::test_entrypoint_refuses_voiceless_identity_then_finish_keeps_error`. Same sidecar serves preview and runtime, so previewed voice == runtime voice by construction. Live: scenarios `self_knowledge`/`ten_turn` speak with the profile's conditionals | PASS (automated) / live PENDING (Task 10 run) | TBD (Task 10 run) |
| 8 | Launched Identity appears as the configured participant in its persistent Default Room | `tests/test_runtime.py` (launch dispatch, `participant_identity == default_room == identity_id`). Live: scenario `default_room` (list_participants shows `ava-001`, name Ava, kind AGENT, relaunch reuses the same room and identity) | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| 9 | Natural conversation, >= 10 coherent voice turns | Live only: scenario `ten_turn` (scripted human audio, real STT/LLM/TTS, no scripted playback of agent speech) | PENDING (Task 10 run) | TBD (Task 10 run) |
| 10 | Barge-in: AI stops promptly, listens, responds to the interruption | Live only: scenario `barge_in` (human speaks over the agent; asserts the agent stops and answers the interruption). Config unit: `tests/test_api.py::test_barge_in_mirrored_in_status` | PENDING (Task 10 run) | TBD (Task 10 run) |
| 11 | Latency evidence: P50/P95/MAX and components over >= 10 turns | Live: scenario `latency` (T0..T4 per turn, `GET /identities/{id}/latency`, writes `latency.md` + `evidence/latency*.json`). Recorder unit tests: `tests/test_runtime.py` (percentiles, latching, T5). See "Latency statement" above | PENDING (Task 10 run) on CPU: expected FAIL-ON-THIS-HOST with the measured bottleneck documented; GPU target PENDING (run on the EC2 host) | TBD (Task 10 run); GPU: TBD (EC2 run) |
| 12 | Phone dogfood via real Service 014 + Exotel | Manual: `docs/PHONE_DOGFOOD.md` (exact runbook). Mocked adapter tests: `tests/test_telephony.py` | **BLOCKED (external)**: needs a real Service 014 deployment, an Exotel number and a LiveKit SIP trunk | n/a until run |
| 13 | Restart Service 015, relaunch Ava, same identity | Unit: `tests/test_api.py::test_restart_persistence`, `tests/test_identities.py::test_persistence_across_connections`. Live: scenario `restart` (stop the API process, start a new one on the same DB, relaunch Ava, compare identity_id/template version/name/persona/voice/default_room) | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| 14 | OpenAPI contract: every operation validates and conforms | `tests/test_openapi_contract.py` (spec validates with openapi-spec-validator; every spec operation exists in the app and vice versa; responses validate against the spec schemas; role annotations match); both copies of `openapi.yaml` kept byte-identical (`cmp openapi.yaml src/service015/openapi.yaml`) | PASS (automated) | pytest output |
| 15 | Fresh expert learning: retrieve a newly verified fact in a fresh session | Offline: `tests/test_expert.py` (ingest, provenance, RBAC, restart persistence, mocked LLM). Live: `uv run python scripts/expert_fresh_learning.py --title ... --body ... --question ... --expect ...` (control ask before ingest, ingest, new app instance + new session, PASS needs the expected text and a citation of the experience row); `tests/test_expert_live.py` with `LLM_API_KEY` | PASS (offline) / live PENDING (needs a real verified fact and `LLM_API_KEY`; run by the controller) | `evidence/expert_fresh_learning.json` (TBD, controller run) |

## Section 27: failure and recovery

| Scenario | Mechanism | Status | Evidence |
|---|---|---|---|
| Voice service unavailable: explicit dependency failure on launch/status, Identity not corrupted, relaunch without recreating | Unit: `tests/test_voices.py::test_launch_rechecks_voice_existence_authorization_and_voicebox` (503 `voicebox_unavailable`, Identity row untouched), `test_instantiate_voicebox_down_is_503_and_persists_nothing`; classification `tts_unavailable` in `tests/test_runtime_lifecycle.py`. Live: scenario `failure` stops and restarts the voice engine | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| LiveKit disconnect: ERROR/RECONNECTING, rejoin same room and identity | Unit: `tests/test_runtime_lifecycle.py::test_room_disconnect_ends_as_error_livekit_disconnected_not_offline`, `test_reconnecting_maps_to_error_and_back`, `test_relaunch_after_livekit_disconnect_rejoins_same_room_and_identity`. Not exercised live (stopping LiveKit would disturb other runs) | PASS (unit); live NOT RUN | pytest output |
| LLM endpoint unavailable: no fabricated answers, error state surfaced, Identity preserved | Unit: `tests/test_runtime_lifecycle.py` (error classification, recoverable vs latched, scoped recovery); `tests/test_expert.py` (503 `llm_unavailable`, nothing stored). Live: scenario `failure` part (b) points the LLM at a broken endpoint and expects presence ERROR `llm_unavailable` with no speech | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| STT failure: reported without replacing self-facts | Unit: `tests/test_runtime_lifecycle.py::test_classify_error_names_the_real_component` (`stt_failure`) and the latch/recovery tests; self-facts come from SQLite, not conversation state | PASS (unit) | pytest output |
| Runtime process crash: restart and relaunch the same Identity from persisted state | Unit: `tests/test_runtime_lifecycle.py` (pidfile adoption, `reconcile_runtime_lost`, staleness to `ERROR heartbeat_lost`/`worker_exited`, relaunch of a stale Identity). Live: scenario `restart` (kill the API process; the worker tree dies with it) | PASS (unit) / PENDING (Task 10 run) | TBD (Task 10 run) |
| Invalid override: rejected with a clear OpenAPI error | `tests/test_api.py::test_test3_...` and `tests/test_identities.py::test_override_not_allowed` (422 `override_not_allowed`, body validated against the spec). Live: scenario `failure` part (a) (disallowed key, non-boolean barge_in, empty name, missing required field -> 422, Identity unchanged) | PASS (automated) / live PENDING (Task 10 run) | pytest output |

## Reproduce

```bash
# Offline suite (contract, units, mocked integrations): the PASS (automated) rows
uv run pytest tests -q --ignore=tests/test_runtime_live.py --ignore=tests/e2e

# Real-stack voice scenarios: the PENDING (Task 10 run) rows. Needs LiveKit, Voicebox, the engine and LLM_API_KEY up.
uv run python scripts/run_acceptance.py --allow-latency-fail      # CPU dev host
uv run python scripts/run_acceptance.py                           # GPU reference host (latency ceiling enforced)

# TEST 15 live
uv run python scripts/expert_fresh_learning.py --help
```

`tests/test_runtime_live.py` is a single heavy live scenario that competes with the acceptance run for the same
LiveKit/engine; run one or the other, not both at once.
