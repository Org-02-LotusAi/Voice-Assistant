# Acceptance run 20261010T094807Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | barge_in | TEST 10 | **FAIL** | 526 s | a next reply was produced; the agent heard the interruption (user_text mentions price); the next reply addresses the interruption (pricing keyword) |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| barge_in | 64.4/100.0 | 279/857.6 | 10562 | 113.0 | 1980.2 | 1488.5 |

## Checks

### barge_in (FAIL)
- PASS: the long answer started -- waited 53.3s
- PASS: the agent was speaking when interrupted -- state=speaking
- PASS: agent audio went silent after the interruption
- PASS: speech onset -> agent audio silent <= 1000 ms -- 375 ms (agent state left 'speaking' after 609 ms)
- FAIL: a next reply was produced
- FAIL: the agent heard the interruption (user_text mentions price) -- Please explain in detail how your Service works from the first call-off, All the way through to the final report, and, it, Take your time.
- FAIL: the next reply addresses the interruption (pricing keyword) -- hits=[] heard=''

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['1ae6b96a-8410-4455-af19-7c58bcdfbd94', '6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 149.7, 'error': None}.
