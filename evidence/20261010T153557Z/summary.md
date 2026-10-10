# Acceptance run 20261010T153557Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | self_knowledge | TEST 4/5 | **PASS** | 187 s | - |
| 2 | isolation | TEST 6 | **PASS** | 245 s | - |
| 3 | ten_turn | TEST 9 | **PASS** | 930 s | - |
| 4 | latency | TEST 11 | **FAIL-ON-THIS-HOST** | 0 s | P50 T0->T4 <= 700 ms and P95 <= 1500 ms (SOW ceiling) |
| 5 | barge_in | TEST 10 | **PASS** | 162 s | - |
| 6 | default_room | TEST 8 | **PASS** | 148 s | - |
| 7 | restart_persistence | TEST 13 | **PASS** | 134 s | - |
| 8 | failure_recovery | SOW 27 | **PASS** | 277 s | - |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| self_knowledge | 78.1/100.0 | 373/587.9 | 10035 | 257.7 | 1898.6 | 1467.8 |
| isolation | 59.8/100.0 | 182/557.5 | 10030 | 161.1 | 2541.5 | 1693.9 |
| ten_turn | 73.6/100.0 | 177/588.3 | 9543 | 448.0 | 2090.4 | 1438.0 |
| barge_in | 79.3/99.8 | 404/522.9 | 9536 | 459.5 | 2046.2 | 654.7 |
| default_room | 22.0/60.6 | 469/829.2 | 9519 | 0.3 | 1916.9 | 748.4 |
| restart_persistence | 60.6/94.4 | 251/604.0 | 9349 | 295.7 | 1916.9 | 1461.9 |
| failure_recovery | 44.2/98.6 | 94/2287.0 | 9422 | 147.2 | 5394.9 | 1462.7 |

## Checks

### self_knowledge (PASS)
- PASS: ava/q_name: reply audio produced -- wait 16.31s
- PASS: ava/q_name: LLM text contains name 'Ava' -- My name is Ava. I work with Safi as an Account Specialist.
- PASS: ava/q_name: transcript of the audio contains name 'Ava' -- heard='My name is Ava. I work with Safi as an account specialist.' llm='My name is Ava. I work with Safi as an Account Specialist.'
- PASS: ava/q_name: no james name 'James'
- PASS: ava/q_name: no james company 'Northwind'
- PASS: ava/q_company: reply audio produced -- wait 13.7s
- PASS: ava/q_company: LLM text contains company 'Safi' -- I work for Safi.
- PASS: ava/q_company: transcript of the audio contains company 'Safi' -- heard='I work for Saffy.' llm='I work for Safi.'
- PASS: ava/q_company: no james name 'James'
- PASS: ava/q_company: no james company 'Northwind'
- PASS: ava/q_role: reply audio produced -- wait 20.12s
- PASS: ava/q_role: LLM text contains role 'Account Specialist' -- I'm an Account Specialist here at Safi.
- PASS: ava/q_role: transcript of the audio contains role 'Account Specialist' -- heard="I'm an account specialist here at Safi." llm="I'm an Account Specialist here at Safi."
- PASS: ava/q_role: no james name 'James'
- PASS: ava/q_role: no james company 'Northwind'
- PASS: greeting played when the human joined -- Hi, this is Ava with Safi. How can I help you today?

### isolation (PASS)
- PASS: different identities -- ava-001 / james-001
- PASS: same template + version
- PASS: different voices
- PASS: different rooms
- PASS: james/q_name: reply audio produced -- wait 12.84s
- PASS: james/q_name: LLM text contains name 'James' -- My name is James.
- PASS: james/q_name: transcript of the audio contains name 'James' -- heard='My name is James.' llm='My name is James.'
- PASS: james/q_name: no ava name 'Ava'
- PASS: james/q_name: no ava company 'Safi'
- PASS: james/q_company: reply audio produced -- wait 12.72s
- PASS: james/q_company: LLM text contains company 'Northwind' -- I work for Northwind.
- PASS: james/q_company: transcript of the audio contains company 'Northwind' -- heard='I work for Northwind.' llm='I work for Northwind.'
- PASS: james/q_company: no ava name 'Ava'
- PASS: james/q_company: no ava company 'Safi'
- PASS: james/q_role: reply audio produced -- wait 18.23s
- PASS: james/q_role: LLM text contains role 'Solutions Consultant' -- I'm a Solutions Consultant.
- PASS: james/q_role: transcript of the audio contains role 'Solutions Consultant' -- heard="I'm a solutions consultant." llm="I'm a Solutions Consultant."
- PASS: james/q_role: no ava name 'Ava'
- PASS: james/q_role: no ava company 'Safi'
- PASS: ava/q_name: reply audio produced -- wait 16.31s
- PASS: ava/q_name: LLM text contains name 'Ava' -- My name is Ava. I work with Safi as an Account Specialist.
- PASS: ava/q_name: transcript of the audio contains name 'Ava' -- heard='My name is Ava. I work with Safi as an account specialist.' llm='My name is Ava. I work with Safi as an Account Specialist.'
- PASS: ava/q_name: no james name 'James'
- PASS: ava/q_name: no james company 'Northwind'
- PASS: ava/q_company: reply audio produced -- wait 13.7s
- PASS: ava/q_company: LLM text contains company 'Safi' -- I work for Safi.
- PASS: ava/q_company: transcript of the audio contains company 'Safi' -- heard='I work for Saffy.' llm='I work for Safi.'
- PASS: ava/q_company: no james name 'James'
- PASS: ava/q_company: no james company 'Northwind'
- PASS: ava/q_role: reply audio produced -- wait 20.12s
- PASS: ava/q_role: LLM text contains role 'Account Specialist' -- I'm an Account Specialist here at Safi.
- PASS: ava/q_role: transcript of the audio contains role 'Account Specialist' -- heard="I'm an account specialist here at Safi." llm="I'm an Account Specialist here at Safi."
- PASS: ava/q_role: no james name 'James'
- PASS: ava/q_role: no james company 'Northwind'

### ten_turn (PASS)
- PASS: >= 10 turns completed -- 10
- PASS: turn_01: reply audio produced -- wait 19.98s
- PASS: turn_01: reply transcribed (non-empty) -- To help me give you the right picture. Can you tell me a bit about your current process and what you're hoping to improve?
- PASS: turn_01: LLM text non-empty -- To help me give you the right picture, can you tell me a bit about your current process and what you're hoping to improve?
- PASS: turn_01: relevant (keyword hit) -- hits=['help'] heard="To help me give you the right picture. Can you tell me a bit about your current process and what you're hoping to improve?"
- PASS: turn_02: reply audio produced -- wait 15.86s
- PASS: turn_02: reply transcribed (non-empty) -- We typically work with businesses that. Need to optimize their account management and customer interactions. Can you tell me a bit about your? Industry and the size of your team?
- PASS: turn_02: LLM text non-empty -- We typically work with businesses that need to optimize their account management and customer interactions.

Can you tell me a bit about your industry and the size of your team?
- PASS: turn_02: relevant (keyword hit) -- hits=['customer', 'team', 'business', 'industr'] heard='We typically work with businesses that. Need to optimize their account management and customer interactions. Can you tell me a bit about your? Industry and the size of your team?'
- PASS: turn_03: reply audio produced -- wait 18.84s
- PASS: turn_03: reply transcribed (non-empty) -- Onboarding timelines vary based on your, Specific needs and integration requirements. So I'll have the right person confirm the exact details in writing for you. In the meantime, Could you tell me what a successful start would look like for your team?
- PASS: turn_03: LLM text non-empty -- Onboarding timelines vary based on your specific needs and integration requirements, so I’ll have the right person confirm the exact details in writing for you.

In the meantime, could you tell me what a successful start would look like for your team?
- PASS: turn_03: relevant (keyword hit) -- hits=['start', 'onboard', 'timeline', 'confirm'] heard="Onboarding timelines vary based on your, Specific needs and integration requirements. So I'll have the right person confirm the exact details in writing for you. In the meantime, Could you tell me what a successful start would look like for your team?"
- PASS: turn_04: reply audio produced -- wait 20.91s
- PASS: turn_04: reply transcribed (non-empty) -- We support many common enterprise tools But to be accurate about your specific stack, I'll have the right person confirm the exact capabilities in writing. Which tools are currently part of your? Workflow,
- PASS: turn_04: LLM text non-empty -- We support many common enterprise tools, but to be accurate about your specific stack, I'll have the right person confirm the exact capabilities in writing.

Which tools are currently part of your workflow?
- PASS: turn_04: relevant (keyword hit) -- hits=['tool', 'stack', 'confirm'] heard="We support many common enterprise tools But to be accurate about your specific stack, I'll have the right person confirm the exact capabilities in writing. Which tools are currently part of your? Workflow,"
- PASS: turn_05: reply audio produced -- wait 13.38s
- PASS: turn_05: reply transcribed (non-empty) -- For a small team, Onboarding is typically streamlined to get you up and running quickly with minimal disruption. To tailor that process to your needs. Could you tell me what your current largest bottleneck is?
- PASS: turn_05: LLM text non-empty -- For a small team, onboarding is typically streamlined to get you up and running quickly with minimal disruption.

To tailor that process to your needs, could you tell me what your current largest bottleneck is?
- PASS: turn_05: relevant (keyword hit) -- hits=['onboard', 'team', 'small'] heard='For a small team, Onboarding is typically streamlined to get you up and running quickly with minimal disruption. To tailor that process to your needs. Could you tell me what your current largest bottleneck is?'
- PASS: turn_06: reply audio produced -- wait 18.05s
- PASS: turn_06: reply transcribed (non-empty) -- Safi takes security and privacy. Seriously by following industry standard best practices but I can't go into specific technical details here. I'll have the right person send you our. Instructions. Current security documentation and writing to ensure you have all the exact information.
- PASS: turn_06: LLM text non-empty -- Safi takes security and privacy seriously by following industry-standard best practices, but I can’t go into specific technical details here.

I’ll have the right person send you our current security documentation in writing to ensure you have all the exact information.
- PASS: turn_06: relevant (keyword hit) -- hits=['secur', 'privacy'] heard="Safi takes security and privacy. Seriously by following industry standard best practices but I can't go into specific technical details here. I'll have the right person send you our. Instructions. Current security documentation and writing to ensure you have all the exact information."
- PASS: turn_07: reply audio produced -- wait 18.75s
- PASS: turn_07: reply transcribed (non-empty) -- I don't have specific information on. Trial terms, so I'll have the right person confirm those details for you in writing. In the meantime, Could you tell me what specific outcome you're hoping to achieve during a trial?
- PASS: turn_07: LLM text non-empty -- I don't have specific information on trial terms, so I'll have the right person confirm those details for you in writing.

In the meantime, could you tell me what specific outcome you're hoping to achieve during a trial?
- PASS: turn_07: relevant (keyword hit) -- hits=['trial', 'confirm'] heard="I don't have specific information on. Trial terms, so I'll have the right person confirm those details for you in writing. In the meantime, Could you tell me what specific outcome you're hoping to achieve during a trial?"
- PASS: turn_08: reply audio produced -- wait 18.75s
- PASS: turn_08: reply transcribed (non-empty) -- We provide ongoing support to ensure. Your team gets the most out of our solution. To ensure I point you to the right resources. Could you tell me what specific aspect of the platform you're most concerned about?
- PASS: turn_08: LLM text non-empty -- We provide ongoing support to ensure your team gets the most out of our solution.

To ensure I point you to the right resources, could you tell me what specific aspect of the platform you're most concerned about?
- PASS: turn_08: relevant (keyword hit) -- hits=['support', 'team'] heard="We provide ongoing support to ensure. Your team gets the most out of our solution. To ensure I point you to the right resources. Could you tell me what specific aspect of the platform you're most concerned about?"
- PASS: turn_09: reply audio produced -- wait 18.16s
- PASS: turn_09: reply transcribed (non-empty) -- I can't share specific pricing figures. As those depend on your unique needs and scale, so I will have the right person confirm the details in writing. To help them prepare a precise proposal. Could you tell me roughly how many users you anticipate needing access?
- PASS: turn_09: LLM text non-empty -- I can't share specific pricing figures, as those depend on your unique needs and scale, so I will have the right person confirm the details in writing.

To help them prepare a precise proposal, could you tell me roughly how many users you anticipate needing access?
- PASS: turn_09: relevant (keyword hit) -- hits=['pric', 'confirm', 'depend'] heard="I can't share specific pricing figures. As those depend on your unique needs and scale, so I will have the right person confirm the details in writing. To help them prepare a precise proposal. Could you tell me roughly how many users you anticipate needing access?"
- PASS: turn_10: reply audio produced -- wait 20.81s
- PASS: turn_10: reply transcribed (non-empty) -- The best next step is a short demo, so I, Can show you how Safi addresses your specific needs. Would you be open to scheduling that for? Later this week,
- PASS: turn_10: LLM text non-empty -- The best next step is a short demo so I can show you how Safi addresses your specific needs.

Would you be open to scheduling that for later this week?
- PASS: turn_10: relevant (keyword hit) -- hits=['next step', 'demo'] heard='The best next step is a short demo, so I, Can show you how Safi addresses your specific needs. Would you be open to scheduling that for? Later this week,'

### latency (FAIL-ON-THIS-HOST)
- PASS: >= 10 turns recorded by the API -- turns=13
- PASS: API report equals report computed from the DB rows
- PASS: t0_t4: >= 10 samples -- count=13
- PASS: t0_t1: >= 10 samples -- count=13
- PASS: t1_t2: >= 10 samples -- count=13
- PASS: t2_t3: >= 10 samples -- count=13
- PASS: t3_t4: >= 10 samples -- count=13
- PASS: t4_t5: >= 10 samples -- count=13
- FAIL: P50 T0->T4 <= 700 ms and P95 <= 1500 ms (SOW ceiling) -- P50=18,188 ms P95=20,750 ms; measured bottleneck: t2_t3 (P50 16,562 ms)

### barge_in (PASS)
- PASS: the long answer started -- waited 17.4s
- PASS: the agent was speaking when interrupted -- state=speaking
- PASS: agent audio went silent after the interruption
- PASS: speech onset -> agent audio silent <= 1000 ms -- 609 ms (agent state left 'speaking' after 640 ms)
- PASS: a next reply was produced
- PASS: the agent heard the interruption (user_text mentions price) -- Sorry, stop there. Can you just tell me the price?
- PASS: the next reply addresses the interruption (pricing keyword) -- hits=['pric', 'cost', 'confirm'] heard='I cannot provide specific pricing figures. As those depend on your unique needs and scale. I will have the right person confirm the Exact costs for you in writing.'

### default_room (PASS)
- PASS: room equals identity default_room on every launch -- ["ava-001", "ava-001", "ava-001"]
- PASS: participant identity equals identity participant_identity on every launch
- PASS: the agent participant is present in that room on every launch -- [{"identity": "ava-001", "name": "Ava", "kind": "PARTICIPANT_KIND_AGENT"}, {"identity": "ava-001", "name": "Ava", "kind": "PARTICIPANT_KIND_AGENT"}, {"identity": "ava-001", "name": "Ava", "kind": "PARTICIPANT_KIND_AGENT"}]
- PASS: room/participant identical across launches
- PASS: identity snapshot unchanged by relaunching

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
- PASS: still answers 'Ava' (audio transcript) -- My name is Ava.
- PASS: still answers 'Ava' (LLM text) -- My name is Ava.

### failure_recovery (PASS)
- PASS: invalid override (disallowed override key) -> 422 -- 422 override_not_allowed
- PASS: invalid override (non-boolean barge_in) -> 422 -- 422 invalid_override
- PASS: invalid override (empty name) -> 422 -- 422 invalid_override
- PASS: invalid override (missing required company) -> 422 -- 422 missing_required_field
- PASS: identity unchanged after invalid overrides
- PASS: no identity was created by rejected instantiates
- PASS: presence became ERROR after the user's turn -- llm_unavailable | llm error: LLMError: type='llm_error' timestamp=1791648446.6215754 label='livekit.plugins.openai.llm.LLM' error=APIConnectionError('Connection error.', body=None, retryable=True) recoverable=False
- PASS: error is llm_unavailable -- llm_unavailable | llm error: LLMError: type='llm_error' timestamp=1791648446.6215754 label='livekit.plugins.openai.llm.LLM' error=APIConnectionError('Connection error.', body=None, retryable=True) recoverable=False
- PASS: no speech after the user's turn (no fabricated reply) -- 0.00s of audio above the RMS gate
- PASS: launch with the engine down -> 503 -- 503
- PASS: error names the dependency failure (voice_engine) -- {"code": "dependency_unavailable", "message": "the voice engine is not healthy (status: unavailable)", "details": {"dependency": "voice_engine", "engine_status": "unavailable"}}
- PASS: identity unchanged by the failed launch
- PASS: presence still OFFLINE after the failed launch -- OFFLINE
- PASS: engine restored and warm -- 133.5s
- PASS: relaunch after restore reaches READY -- 42.4s
- PASS: identity unchanged after recovery
- PASS: engine still up and warm at the end -- {"status": "ok", "engine": "chatterbox-turbo", "variant": "turbo", "device": "cpu", "sample_rate": 24000, "warm": true, "cached_profiles": ["6326829e-38ae-475f-aedb-5c293aa43e51"], "nano_supported": true, "load_seconds": 31.4, "error": null}

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 31.4, 'error': None}.
