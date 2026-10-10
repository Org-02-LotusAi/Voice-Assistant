# Acceptance run 20261010T092450Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | ten_turn | TEST 9 | **FAIL** | 1374 s | turn_03: reply audio produced; turn_03: reply transcribed (non-empty); turn_03: LLM text non-empty; turn_03: relevant (keyword hit); turn_07: reply audio produced; turn_07: reply transcribed (non-empty); turn_07: LLM text non-empty; turn_07: relevant (keyword hit) |
| 2 | latency | TEST 11 | **FAIL** | 0 s | >= 10 turns recorded by the API; t0_t4: >= 10 samples; t0_t1: >= 10 samples; t1_t2: >= 10 samples; t2_t3: >= 10 samples; t3_t4: >= 10 samples; t4_t5: >= 10 samples; P50 T0->T4 <= 700 ms and P95 <= 1500 ms (SOW ceiling) |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| ten_turn | 73.9/100.0 | 15/1191.2 | 11874 | 263.4 | 2056.3 | 1530.1 |

## Checks

### ten_turn (FAIL)
- PASS: >= 10 turns completed -- 10
- PASS: turn_01: reply audio produced -- wait 32.24s
- PASS: turn_01: reply transcribed (non-empty) -- Thank you for confirming that you are a fit. What specific business goals are you? Looking to achieve with this solution?
- PASS: turn_01: LLM text non-empty -- Thank you for confirming that you are a fit. What specific business goals are you looking to achieve with this solution?
- PASS: turn_01: relevant (keyword hit) -- hits=['solution'] heard='Thank you for confirming that you are a fit. What specific business goals are you? Looking to achieve with this solution?'
- PASS: turn_02: reply audio produced -- wait 16.75s
- PASS: turn_02: reply transcribed (non-empty) -- We typically work with mid-sized teams. Looking to streamline their operations. Who is the primary decision-maker? Involved in your current process?
- PASS: turn_02: LLM text non-empty -- We typically work with mid-sized teams looking to streamline their operations. Who is the primary decision-maker involved in your current process?
- PASS: turn_02: relevant (keyword hit) -- hits=['team'] heard='We typically work with mid-sized teams. Looking to streamline their operations. Who is the primary decision-maker? Involved in your current process?'
- FAIL: turn_03: reply audio produced -- wait Nones
- FAIL: turn_03: reply transcribed (non-empty)
- FAIL: turn_03: LLM text non-empty
- FAIL: turn_03: relevant (keyword hit) -- hits=[] heard=''
- PASS: turn_04: reply audio produced -- wait 15.86s
- PASS: turn_04: reply transcribed (non-empty) -- We integrate with many common business. Tools to fit your existing workflow. Could you share which platforms you? Currently rely on most,
- PASS: turn_04: LLM text non-empty -- We integrate with many common business tools to fit your existing workflow. Could you share which platforms you currently rely on most?
- PASS: turn_04: relevant (keyword hit) -- hits=['integrat', 'tool'] heard='We integrate with many common business. Tools to fit your existing workflow. Could you share which platforms you? Currently rely on most,'
- PASS: turn_05: reply audio produced -- wait 39.7s
- PASS: turn_05: reply transcribed (non-empty) -- Onboarding is designed to be quick, And, Guided so your team can start using the platform with minimal disruption. I can schedule a short call with our. Onboarding specialist to walk you through the specific steps for your size
- PASS: turn_05: LLM text non-empty -- Onboarding is designed to be quick and guided, so your team can start using the platform with minimal disruption. I can schedule a short call with our onboarding specialist to walk you through the specific steps for your size.
- PASS: turn_05: relevant (keyword hit) -- hits=['onboard', 'team', 'start', 'walk'] heard='Onboarding is designed to be quick, And, Guided so your team can start using the platform with minimal disruption. I can schedule a short call with our. Onboarding specialist to walk you through the specific steps for your size'
- PASS: turn_06: reply audio produced -- wait 51.94s
- PASS: turn_06: reply transcribed (non-empty) -- We strictly follow privacy standards, too. Ensure your data remains secure and confidential. Could you click on it? Clarify which specific aspect, Of privacy or data handling is your biggest concern?
- PASS: turn_06: LLM text non-empty -- We strictly follow privacy standards to ensure your data remains secure and confidential. Could you clarify which specific aspect of privacy or data handling is your biggest concern?
- PASS: turn_06: relevant (keyword hit) -- hits=['secur', 'privacy', 'data'] heard='We strictly follow privacy standards, too. Ensure your data remains secure and confidential. Could you click on it? Clarify which specific aspect, Of privacy or data handling is your biggest concern?'
- FAIL: turn_07: reply audio produced -- wait Nones
- FAIL: turn_07: reply transcribed (non-empty)
- FAIL: turn_07: LLM text non-empty
- FAIL: turn_07: relevant (keyword hit) -- hits=[] heard=''
- PASS: turn_08: reply audio produced -- wait 37.2s
- PASS: turn_08: reply transcribed (non-empty) -- We provide dedicated support to ensure, Your team succeeds from day one. Would a short demo be a good next step? To show you how that support works in practice?
- PASS: turn_08: LLM text non-empty -- We provide dedicated support to ensure your team succeeds from day one. Would a short demo be a good next step to show you how that support works in practice?
- PASS: turn_08: relevant (keyword hit) -- hits=['support', 'team'] heard='We provide dedicated support to ensure, Your team succeeds from day one. Would a short demo be a good next step? To show you how that support works in practice?'
- PASS: turn_09: reply audio produced -- wait 24.98s
- PASS: turn_09: reply transcribed (non-empty) -- I don't have the exact pricing details. Available in this conversation, so I will have the right person confirm the structure in writing for you.
- PASS: turn_09: LLM text non-empty -- I don't have the exact pricing details available in this conversation, so I will have the right person confirm the structure in writing for you.
- PASS: turn_09: relevant (keyword hit) -- hits=['pric', 'confirm'] heard="I don't have the exact pricing details. Available in this conversation, so I will have the right person confirm the structure in writing for you."
- PASS: turn_10: reply audio produced -- wait 67.14s
- PASS: turn_10: reply transcribed (non-empty) -- A short demo to see how Safi fits your, Current workflow would be the best next step. I can send a calendar invite to schedule. That at your convenience,
- PASS: turn_10: LLM text non-empty -- A short demo to see how Safi fits your current workflow would be the best next step. I can send a calendar invite to schedule that at your convenience.
- PASS: turn_10: relevant (keyword hit) -- hits=['next step', 'demo', 'schedule', 'calendar', 'send'] heard='A short demo to see how Safi fits your, Current workflow would be the best next step. I can send a calendar invite to schedule. That at your convenience,'

### latency (FAIL)
- FAIL: >= 10 turns recorded by the API -- turns=8
- PASS: API report equals report computed from the DB rows
- FAIL: t0_t4: >= 10 samples -- count=8
- FAIL: t0_t1: >= 10 samples -- count=8
- FAIL: t1_t2: >= 10 samples -- count=8
- FAIL: t2_t3: >= 10 samples -- count=8
- FAIL: t3_t4: >= 10 samples -- count=8
- FAIL: t4_t5: >= 10 samples -- count=8
- FAIL: P50 T0->T4 <= 700 ms and P95 <= 1500 ms (SOW ceiling) -- P50=31,906 ms P95=66,859 ms; measured bottleneck: t2_t3 (P50 25,516 ms)

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['1ae6b96a-8410-4455-af19-7c58bcdfbd94', '6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 149.7, 'error': None}.
