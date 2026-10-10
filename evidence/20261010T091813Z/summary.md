# Acceptance run 20261010T091813Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | self_knowledge | TEST 4/5 | **PASS-AUDIO-CAVEAT** | 140 s | ava/q_company: transcript of the audio contains company 'Safi' |
| 2 | isolation | TEST 6 | **PASS-AUDIO-CAVEAT** | 238 s | ava/q_company: transcript of the audio contains company 'Safi' |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| self_knowledge | 64.0/100.0 | 88/406.2 | 8484 | 267.0 | 1908.5 | 1526.0 |
| isolation | 48.0/94.4 | 123/321.1 | 8469 | 168.3 | 2645.4 | 1803.0 |

## Checks

### self_knowledge (PASS-AUDIO-CAVEAT)
- PASS: ava/q_name: reply audio produced -- wait 12.28s
- PASS: ava/q_name: LLM text contains name 'Ava' -- My name is Ava.
- PASS: ava/q_name: transcript of the audio contains name 'Ava' -- heard='My name is Ava.' llm='My name is Ava.'
- PASS: ava/q_name: no james name 'James'
- PASS: ava/q_name: no james company 'Northwind'
- PASS: ava/q_company: reply audio produced -- wait 13.12s
- PASS: ava/q_company: LLM text contains company 'Safi' -- I work for Safi.
- FAIL: ava/q_company: transcript of the audio contains company 'Safi' -- heard='I work for Sethie.' llm='I work for Safi.'
- PASS: ava/q_company: no james name 'James'
- PASS: ava/q_company: no james company 'Northwind'
- PASS: ava/q_role: reply audio produced -- wait 18.05s
- PASS: ava/q_role: LLM text contains role 'Account Specialist' -- I'm an Account Specialist at Safi.
- PASS: ava/q_role: transcript of the audio contains role 'Account Specialist' -- heard="I'm an account specialist at Safi." llm="I'm an Account Specialist at Safi."
- PASS: ava/q_role: no james name 'James'
- PASS: ava/q_role: no james company 'Northwind'
- PASS: greeting played when the human joined -- Hi. This is Ava with Safi. How can I help you today?

### isolation (PASS-AUDIO-CAVEAT)
- PASS: different identities -- ava-001 / james-001
- PASS: same template + version
- PASS: different voices
- PASS: different rooms
- PASS: james/q_name: reply audio produced -- wait 12.47s
- PASS: james/q_name: LLM text contains name 'James' -- My name is James.
- PASS: james/q_name: transcript of the audio contains name 'James' -- heard='you My name is James.' llm='My name is James.'
- PASS: james/q_name: no ava name 'Ava'
- PASS: james/q_name: no ava company 'Safi'
- PASS: james/q_company: reply audio produced -- wait 13.44s
- PASS: james/q_company: LLM text contains company 'Northwind' -- I work for Northwind.
- PASS: james/q_company: transcript of the audio contains company 'Northwind' -- heard='I work for Northwind.' llm='I work for Northwind.'
- PASS: james/q_company: no ava name 'Ava'
- PASS: james/q_company: no ava company 'Safi'
- PASS: james/q_role: reply audio produced -- wait 14.78s
- PASS: james/q_role: LLM text contains role 'Solutions Consultant' -- I am a Solutions Consultant.
- PASS: james/q_role: transcript of the audio contains role 'Solutions Consultant' -- heard='I am a solutions consultant.' llm='I am a Solutions Consultant.'
- PASS: james/q_role: no ava name 'Ava'
- PASS: james/q_role: no ava company 'Safi'
- PASS: ava/q_name: reply audio produced -- wait 12.28s
- PASS: ava/q_name: LLM text contains name 'Ava' -- My name is Ava.
- PASS: ava/q_name: transcript of the audio contains name 'Ava' -- heard='My name is Ava.' llm='My name is Ava.'
- PASS: ava/q_name: no james name 'James'
- PASS: ava/q_name: no james company 'Northwind'
- PASS: ava/q_company: reply audio produced -- wait 13.12s
- PASS: ava/q_company: LLM text contains company 'Safi' -- I work for Safi.
- FAIL: ava/q_company: transcript of the audio contains company 'Safi' -- heard='I work for Sethie.' llm='I work for Safi.'
- PASS: ava/q_company: no james name 'James'
- PASS: ava/q_company: no james company 'Northwind'
- PASS: ava/q_role: reply audio produced -- wait 18.05s
- PASS: ava/q_role: LLM text contains role 'Account Specialist' -- I'm an Account Specialist at Safi.
- PASS: ava/q_role: transcript of the audio contains role 'Account Specialist' -- heard="I'm an account specialist at Safi." llm="I'm an Account Specialist at Safi."
- PASS: ava/q_role: no james name 'James'
- PASS: ava/q_role: no james company 'Northwind'

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51', '1ae6b96a-8410-4455-af19-7c58bcdfbd94'], 'nano_supported': True, 'load_seconds': 149.7, 'error': None}.
