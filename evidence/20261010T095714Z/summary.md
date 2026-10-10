# Acceptance run 20261010T095714Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | default_room | TEST 8 | **PASS** | 45 s | - |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| default_room | 56.5/67.7 | 1282/1778.1 | 10366 | 0.3 | 1839.0 | 885.9 |

## Checks

### default_room (PASS)
- PASS: room equals identity default_room on every launch -- ["ava-001", "ava-001", "ava-001"]
- PASS: participant identity equals identity participant_identity on every launch
- PASS: the agent participant is present in that room on every launch -- [{"identity": "ava-001", "name": "Ava", "kind": "PARTICIPANT_KIND_AGENT"}, {"identity": "ava-001", "name": "Ava", "kind": "PARTICIPANT_KIND_AGENT"}, {"identity": "ava-001", "name": "Ava", "kind": "PARTICIPANT_KIND_AGENT"}]
- PASS: room/participant identical across launches
- PASS: identity snapshot unchanged by relaunching

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['1ae6b96a-8410-4455-af19-7c58bcdfbd94', '6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 149.7, 'error': None}.
