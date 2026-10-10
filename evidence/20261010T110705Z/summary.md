# Acceptance run 20261010T110705Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | barge_in | TEST 10 | **PASS** | 255 s | - |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| barge_in | 73.4/99.9 | 364/618.2 | 8377 | 401.9 | 1978.9 | 1547.1 |

## Checks

### barge_in (PASS)
- PASS: the long answer started -- waited 26.8s
- PASS: the agent was speaking when interrupted -- state=speaking
- PASS: agent audio went silent after the interruption
- PASS: speech onset -> agent audio silent <= 1000 ms -- 687 ms (agent state left 'speaking' after 593 ms)
- PASS: a next reply was produced
- PASS: the agent heard the interruption (user_text mentions price) -- Sorry. Stop there! Can you just tell me the price?
- PASS: the next reply addresses the interruption (pricing keyword) -- hits=['pric', 'cost', 'confirm'] heard="I don't have specific pricing. Information available to share right now I can have the right person confirm the, Exact costs in writing for you. Would you like me to set that up?"

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 42.3, 'error': None}.
