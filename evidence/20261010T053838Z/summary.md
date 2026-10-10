# Acceptance run 20261010T053838Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | self_knowledge | TEST 4/5 | **FAIL** | 178 s | ava/q_company: transcript contains company 'Safi' |
| 2 | isolation | TEST 6 | **FAIL** | 291 s | ava/q_company: transcript contains company 'Safi' |
| 3 | ten_turn | TEST 9 | **FAIL** | 1791 s | turn_09: reply audio produced; turn_09: reply transcribed (non-empty); turn_09: LLM text non-empty; turn_09: relevant (keyword hit); turn_10: relevant (keyword hit) |
| 4 | latency | TEST 11 | **FAIL-ON-THIS-HOST** | 0 s | P50 T0->T4 <= 700 ms and P95 <= 1500 ms (SOW ceiling) |
| 5 | barge_in | TEST 10 | **FAIL** | 110 s | speech onset -> agent audio silent <= 1000 ms; the agent heard the interruption (user_text mentions price); the next reply addresses the interruption (pricing keyword) |
| 6 | default_room | - | **FAIL** | 507 s | ERROR: AssertionError: ava-001 went to ERROR during launch: start_timeout / still STARTING after 120 s |
| 7 | restart_persistence | TEST 13 | **PASS** | 235 s | - |
| 8 | failure | - | **FAIL** | 697 s | ERROR: AssertionError: launch ava-001 -> 503: {"error":{"code":"runtime_unavailable","message":"the runtime worker did not regi |

## Checks

### self_knowledge (FAIL)
- PASS: ava/q_name: reply audio produced -- wait 11.56s
- PASS: ava/q_name: transcript contains name 'Ava' -- My name is Ava.
- PASS: ava/q_name: LLM text contains name 'Ava' -- My name is Ava.
- PASS: ava/q_name: no james name 'James'
- PASS: ava/q_name: no james company 'Northwind'
- PASS: ava/q_company: reply audio produced -- wait 19.36s
- FAIL: ava/q_company: transcript contains company 'Safi' -- I work for SaaS. See, I work.
- PASS: ava/q_company: LLM text contains company 'Safi' -- I work for Safi.
- PASS: ava/q_company: no james name 'James'
- PASS: ava/q_company: no james company 'Northwind'
- PASS: ava/q_role: reply audio produced -- wait 17.47s
- PASS: ava/q_role: transcript contains role 'Account Specialist' -- I'm an account specialist.
- PASS: ava/q_role: LLM text contains role 'Account Specialist' -- I'm an Account Specialist.
- PASS: ava/q_role: no james name 'James'
- PASS: ava/q_role: no james company 'Northwind'
- PASS: greeting played when the human joined -- Hi, this is Ava with Safi. How can I help you today?

### isolation (FAIL)
- PASS: different identities -- ava-001 / james-001
- PASS: same template + version
- PASS: different voices
- PASS: different rooms
- PASS: james/q_name: reply audio produced -- wait 14.11s
- PASS: james/q_name: transcript contains name 'James' -- My name is James.
- PASS: james/q_name: LLM text contains name 'James' -- My name is James.
- PASS: james/q_name: no ava name 'Ava'
- PASS: james/q_name: no ava company 'Safi'
- PASS: james/q_company: reply audio produced -- wait 19.05s
- PASS: james/q_company: transcript contains company 'Northwind' -- I work for North Wind.
- PASS: james/q_company: LLM text contains company 'Northwind' -- I work for Northwind.
- PASS: james/q_company: no ava name 'Ava'
- PASS: james/q_company: no ava company 'Safi'
- PASS: james/q_role: reply audio produced -- wait 33.0s
- PASS: james/q_role: transcript contains role 'Solutions Consultant' -- I'm a solutions consultant at Northwind.
- PASS: james/q_role: LLM text contains role 'Solutions Consultant' -- I’m a Solutions Consultant at Northwind.
- PASS: james/q_role: no ava name 'Ava'
- PASS: james/q_role: no ava company 'Safi'
- PASS: ava/q_name: reply audio produced -- wait 11.56s
- PASS: ava/q_name: transcript contains name 'Ava' -- My name is Ava.
- PASS: ava/q_name: LLM text contains name 'Ava' -- My name is Ava.
- PASS: ava/q_name: no james name 'James'
- PASS: ava/q_name: no james company 'Northwind'
- PASS: ava/q_company: reply audio produced -- wait 19.36s
- FAIL: ava/q_company: transcript contains company 'Safi' -- I work for SaaS. See, I work.
- PASS: ava/q_company: LLM text contains company 'Safi' -- I work for Safi.
- PASS: ava/q_company: no james name 'James'
- PASS: ava/q_company: no james company 'Northwind'
- PASS: ava/q_role: reply audio produced -- wait 17.47s
- PASS: ava/q_role: transcript contains role 'Account Specialist' -- I'm an account specialist.
- PASS: ava/q_role: LLM text contains role 'Account Specialist' -- I'm an Account Specialist.
- PASS: ava/q_role: no james name 'James'
- PASS: ava/q_role: no james company 'Northwind'

### ten_turn (FAIL)
- PASS: >= 10 turns completed -- 10
- PASS: turn_01: reply audio produced -- wait 60.36s
- PASS: turn_01: reply transcribed (non-empty) -- I'm an account specialist at Safi. And I focus on helping customers solve specific business challenges through our tailored solutions. To offer you the most relevant details, Could you tell me a bit about what your team is currently working on?
- PASS: turn_01: LLM text non-empty -- I'm an Account Specialist at Safi, and I focus on helping customers solve specific business challenges through our tailored solutions. To offer you the most relevant details, could you tell me a bit about what your team is currently working on?
- PASS: turn_01: relevant (keyword hit) -- hits=['safi', 'help', 'offer', 'solution', 'work'] heard="I'm an account specialist at Safi. And I focus on helping customers solve specific business challenges through our tailored solutions. To offer you the most relevant details, Could you tell me a bit about what your team is currently working on?"
- PASS: turn_02: reply audio produced -- wait 23.28s
- PASS: turn_02: reply transcribed (non-empty) -- We typically work with businesses, Looking to streamline their account management and improve customer retention. Who do you currently serve? Or, what industry are you in?
- PASS: turn_02: LLM text non-empty -- We typically work with businesses looking to streamline their account management and improve customer retention. Who do you currently serve, or what industry are you in?
- PASS: turn_02: relevant (keyword hit) -- hits=['customer', 'business', 'industr'] heard='We typically work with businesses, Looking to streamline their account management and improve customer retention. Who do you currently serve? Or, what industry are you in?'
- PASS: turn_03: reply audio produced -- wait 18.72s
- PASS: turn_03: reply transcribed (non-empty) -- The timeline really depends on your. Specific needs and how quickly we can align on the details. I can have the right person confirm the, Exact onboarding schedule and writing for you.
- PASS: turn_03: LLM text non-empty -- The timeline really depends on your specific needs and how quickly we can align on the details. I can have the right person confirm the exact onboarding schedule in writing for you.
- PASS: turn_03: relevant (keyword hit) -- hits=['quick', 'onboard', 'timeline', 'depend', 'confirm'] heard='The timeline really depends on your. Specific needs and how quickly we can align on the details. I can have the right person confirm the, Exact onboarding schedule and writing for you.'
- PASS: turn_04: reply audio produced -- wait 53.55s
- PASS: turn_04: reply transcribed (non-empty) -- We often integrate with common platforms. But it depends on the specific tools you're using. Which systems are currently part of your? Work flow
- PASS: turn_04: LLM text non-empty -- We often integrate with common platforms, but it depends on the specific tools you're using. Which systems are currently part of your workflow?
- PASS: turn_04: relevant (keyword hit) -- hits=['integrat', 'tool', 'system', 'depend'] heard="We often integrate with common platforms. But it depends on the specific tools you're using. Which systems are currently part of your? Work flow"
- PASS: turn_05: reply audio produced -- wait 65.45s
- PASS: turn_05: reply transcribed (non-empty) -- I don't have the specific onboarding. Steps for small teams in front of me right now. I can have the right person send you away. Detailed outline and writing so you know exactly what to expect.
- PASS: turn_05: LLM text non-empty -- I don't have the specific onboarding steps for small teams in front of me right now. I can have the right person send you a detailed outline in writing so you know exactly what to expect.
- PASS: turn_05: relevant (keyword hit) -- hits=['onboard', 'team', 'small'] heard="I don't have the specific onboarding. Steps for small teams in front of me right now. I can have the right person send you away. Detailed outline and writing so you know exactly what to expect."
- PASS: turn_06: reply audio produced -- wait 91.62s
- PASS: turn_06: reply transcribed (non-empty) -- I don't have the detailed security. Specifications available in our current conversation. I can have the right person send you our. Official privacy and security documentation for review
- PASS: turn_06: LLM text non-empty -- I don't have the detailed security specifications available in our current conversation. I can have the right person send you our official privacy and security documentation for review.
- PASS: turn_06: relevant (keyword hit) -- hits=['secur', 'privacy'] heard="I don't have the detailed security. Specifications available in our current conversation. I can have the right person send you our. Official privacy and security documentation for review"
- PASS: turn_07: reply audio produced -- wait 39.03s
- PASS: turn_07: reply transcribed (non-empty) -- I don't know. Have the current trial policy. Details to confirm for you. I can have the right person clarify that. In writing, so you have the accurate information.
- PASS: turn_07: LLM text non-empty -- I don't have the current trial policy details to confirm for you. I can have the right person clarify that in writing so you have the accurate information.
- PASS: turn_07: relevant (keyword hit) -- hits=['trial', 'confirm'] heard="I don't know. Have the current trial policy. Details to confirm for you. I can have the right person clarify that. In writing, so you have the accurate information."
- PASS: turn_08: reply audio produced -- wait 36.52s
- PASS: turn_08: reply transcribed (non-empty) -- I don't have the specific support tier.
- PASS: turn_08: LLM text non-empty -- I don't have the specific support tier details to share right now. I can have the right person send you an overview of our support options in writing.
- PASS: turn_08: relevant (keyword hit) -- hits=['support'] heard="I don't have the specific support tier."
- FAIL: turn_09: reply audio produced -- wait Nones
- FAIL: turn_09: reply transcribed (non-empty)
- FAIL: turn_09: LLM text non-empty
- FAIL: turn_09: relevant (keyword hit) -- hits=[] heard=''
- PASS: turn_10: reply audio produced -- wait 94.31s
- PASS: turn_10: reply transcribed (non-empty) -- I'd love to set up a brief. Ultimate. Map out it. Thank you. Exactly how Safi can fit your work for short chat. This week,
- PASS: turn_10: LLM text non-empty -- I’d love to set up a brief follow-up call to map out exactly how Safi can fit your workflows. Would you be available for a short chat this week?
- FAIL: turn_10: relevant (keyword hit) -- hits=[] heard="I'd love to set up a brief. Ultimate. Map out it. Thank you. Exactly how Safi can fit your work for short chat. This week,"

### latency (FAIL-ON-THIS-HOST)
- PASS: >= 10 turns recorded by the API -- turns=12
- PASS: API report equals report computed from the DB rows
- PASS: t0_t4: >= 10 samples -- count=12
- PASS: t0_t1: >= 10 samples -- count=12
- PASS: t1_t2: >= 10 samples -- count=12
- PASS: t2_t3: >= 10 samples -- count=12
- PASS: t3_t4: >= 10 samples -- count=12
- PASS: t4_t5: >= 10 samples -- count=12
- FAIL: P50 T0->T4 <= 700 ms and P95 <= 1500 ms (SOW ceiling) -- P50=34,141 ms P95=90,687 ms; measured bottleneck: t2_t3 (P50 24,578 ms)

### barge_in (FAIL)
- PASS: the long answer started -- waited 62.3s
- PASS: the agent was speaking when interrupted -- state=speaking
- PASS: agent audio went silent after the interruption
- FAIL: speech onset -> agent audio silent <= 1000 ms -- 11015 ms (agent state left 'speaking' after 8922 ms)
- PASS: a next reply was produced
- FAIL: the agent heard the interruption (user_text mentions price) -- Please explain in detail how your service works, from the first call all. All the way through. To the final report and take your time
- FAIL: the next reply addresses the interruption (pricing keyword) -- hits=[] heard='This'

### default_room (FAIL)
- ERROR: `AssertionError: ava-001 went to ERROR during launch: start_timeout / still STARTING after 120 s`

### restart_persistence (PASS)
- PASS: API + runtime stopped -- {"graceful": true, "returncode": 3}
- PASS: runtime worker did not survive the API -- {"graceful": true, "returncode": 3}
- PASS: identity_id unchanged
- PASS: template_id/template_version unchanged -- v1
- PASS: name/company/role (self) unchanged
- PASS: persona unchanged
- PASS: voice unchanged -- {"profile_id": "6326829e-38ae-475f-aedb-5c293aa43e51", "engine": "chatterbox-turbo"}
- PASS: default_room/participant_identity unchanged
- PASS: resolved_hash identical -- f01eb6de1cb75fff1b1a70d080bedb82f4df741aea39f7e12ba6e3c3bd606eca
- PASS: whole snapshot identical
- PASS: template versions unchanged
- PASS: presence after restart is OFFLINE (stale runtime reconciled) -- OFFLINE / None
- PASS: relaunched Ava reaches READY/BUSY -- BUSY
- PASS: still answers 'Ava' (audio transcript) -- My name is Ava,
- PASS: still answers 'Ava' (LLM text) -- My name is Ava.

### failure (FAIL)
- ERROR: `AssertionError: launch ava-001 -> 503: {"error":{"code":"runtime_unavailable","message":"the runtime worker did not register with LiveKit within 60 s","details":{}}}`

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 149.7, 'error': None}.
