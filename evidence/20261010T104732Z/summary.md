# Acceptance run 20261010T104732Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | ten_turn | TEST 9 | **PASS** | 1116 s | - |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| ten_turn | 77.3/100.0 | 153/645.9 | 8279 | 415.6 | 2091.7 | 1520.4 |

## Checks

### ten_turn (PASS)
- PASS: >= 10 turns completed -- 10
- PASS: turn_01: reply audio produced -- wait 26.58s
- PASS: turn_01: reply transcribed (non-empty) -- Safi provides solutions to help account. Specialists and sales teams streamline their processes and improve customer interactions. Could you tell me a bit about your? Current setup and what goals you're trying to achieve.
- PASS: turn_01: LLM text non-empty -- Safi provides solutions to help account specialists and sales teams streamline their processes and improve customer interactions. Could you tell me a bit about your current setup and what goals you're trying to achieve?
- PASS: turn_01: relevant (keyword hit) -- hits=['safi', 'help', 'provid', 'solution'] heard="Safi provides solutions to help account. Specialists and sales teams streamline their processes and improve customer interactions. Could you tell me a bit about your? Current setup and what goals you're trying to achieve."
- PASS: turn_02: reply audio produced -- wait 16.19s
- PASS: turn_02: reply transcribed (non-empty) -- We typically work with mid-sized. Companies looking to optimize their sales and account management workflows, Does that sound like the kind of? Organization you're part of?
- PASS: turn_02: LLM text non-empty -- We typically work with mid-sized companies looking to optimize their sales and account management workflows. Does that sound like the kind of organization you’re part of?
- PASS: turn_02: relevant (keyword hit) -- hits=['compan', 'organi'] heard="We typically work with mid-sized. Companies looking to optimize their sales and account management workflows, Does that sound like the kind of? Organization you're part of?"
- PASS: turn_03: reply audio produced -- wait 26.56s
- PASS: turn_03: reply transcribed (non-empty) -- Onboarding timelines can vary based on, Your specific needs and team size. I'd recommend scheduling a quick call. With our solutions team to get a precise estimate for your situation. Would you be open to setting that up?
- PASS: turn_03: LLM text non-empty -- Onboarding timelines can vary based on your specific needs and team size. I’d recommend scheduling a quick call with our solutions team to get a precise estimate for your situation. Would you be open to setting that up?
- PASS: turn_03: relevant (keyword hit) -- hits=['quick', 'onboard', 'timeline'] heard="Onboarding timelines can vary based on, Your specific needs and team size. I'd recommend scheduling a quick call. With our solutions team to get a precise estimate for your situation. Would you be open to setting that up?"
- PASS: turn_04: reply audio produced -- wait 18.7s
- PASS: turn_04: reply transcribed (non-empty) -- We do integrate with many popular sales. NCRM platforms to ensure a smooth workflow. Could you share which specific tools? Your team relies on most,
- PASS: turn_04: LLM text non-empty -- We do integrate with many popular sales and CRM platforms to ensure a smooth workflow. Could you share which specific tools your team relies on most?
- PASS: turn_04: relevant (keyword hit) -- hits=['integrat', 'tool'] heard='We do integrate with many popular sales. NCRM platforms to ensure a smooth workflow. Could you share which specific tools? Your team relies on most,'
- PASS: turn_05: reply audio produced -- wait 13.56s
- PASS: turn_05: reply transcribed (non-empty) -- For a small team, Onboarding is usually streamlined to get you up and running quickly without a heavy lift. To give you a concrete picture of the, Timeline and steps, could you tell me roughly how many people on your team would be using the system?
- PASS: turn_05: LLM text non-empty -- For a small team, onboarding is usually streamlined to get you up and running quickly without a heavy lift. To give you a concrete picture of the timeline and steps, could you tell me roughly how many people on your team would be using the system?
- PASS: turn_05: relevant (keyword hit) -- hits=['onboard', 'team', 'small'] heard='For a small team, Onboarding is usually streamlined to get you up and running quickly without a heavy lift. To give you a concrete picture of the, Timeline and steps, could you tell me roughly how many people on your team would be using the system?'
- PASS: turn_06: reply audio produced -- wait 19.16s
- PASS: turn_06: reply transcribed (non-empty) -- We take data security seriously and, Follow industry standard practices to keep your information safe. Since specific compliance details vary, By region and industry, I can have our security team send you our full documentation and writing for your review. Does that work for you?
- PASS: turn_06: LLM text non-empty -- We take data security seriously and follow industry-standard practices to keep your information safe. Since specific compliance details vary by region and industry, I can have our security team send you our full documentation in writing for your review. Does that work for you?
- PASS: turn_06: relevant (keyword hit) -- hits=['secur', 'data', 'complian', 'safe'] heard='We take data security seriously and, Follow industry standard practices to keep your information safe. Since specific compliance details vary, By region and industry, I can have our security team send you our full documentation and writing for your review. Does that work for you?'
- PASS: turn_07: reply audio produced -- wait 19.59s
- PASS: turn_07: reply transcribed (non-empty) -- We don't offer a standard self-serve trial. But we can arrange a hands-on demonstration so you can see how it fits your workflow. Would you be open to a short demo call? This week to evaluate that,
- PASS: turn_07: LLM text non-empty -- We don’t offer a standard self-serve trial, but we can arrange a hands-on demonstration so you can see how it fits your workflow. Would you be open to a short demo call this week to evaluate that?
- PASS: turn_07: relevant (keyword hit) -- hits=['trial', 'demo'] heard="We don't offer a standard self-serve trial. But we can arrange a hands-on demonstration so you can see how it fits your workflow. Would you be open to a short demo call? This week to evaluate that,"
- PASS: turn_08: reply audio produced -- wait 18.44s
- PASS: turn_08: reply transcribed (non-empty) -- We provide dedicated support to guide Your team through initial setup and ongoing questions. To ensure you get the right level of. Assistance. Could you tell me who typically handles technical issues in your organization?
- PASS: turn_08: LLM text non-empty -- We provide dedicated support to guide your team through initial setup and ongoing questions. To ensure you get the right level of assistance, could you tell me who typically handles technical issues in your organization?
- PASS: turn_08: relevant (keyword hit) -- hits=['support', 'team', 'assist'] heard='We provide dedicated support to guide Your team through initial setup and ongoing questions. To ensure you get the right level of. Assistance. Could you tell me who typically handles technical issues in your organization?'
- PASS: turn_09: reply audio produced -- wait 16.81s
- PASS: turn_09: reply transcribed (non-empty) -- I don't have the specific pricing Details to share right now, so I can have our sales team send you accurate written information. Would you be open to a brief follow-up? Call to discuss your budget and needs?
- PASS: turn_09: LLM text non-empty -- I don't have the specific pricing details to share right now, so I can have our sales team send you accurate written information. Would you be open to a brief follow-up call to discuss your budget and needs?
- PASS: turn_09: relevant (keyword hit) -- hits=['pric', 'budget', 'written'] heard="I don't have the specific pricing Details to share right now, so I can have our sales team send you accurate written information. Would you be open to a brief follow-up? Call to discuss your budget and needs?"
- PASS: turn_10: reply audio produced -- wait 21.31s
- PASS: turn_10: reply transcribed (non-empty) -- The best next step is a short demo. Tailored to your team's specific tools. I can set that up for this week. Did the call on Tuesday or Wednesday work? For you?
- PASS: turn_10: LLM text non-empty -- The best next step is a short demo tailored to your team’s specific tools. I can set that up for this week. Does a call on Tuesday or Wednesday work for you?
- PASS: turn_10: relevant (keyword hit) -- hits=['next step', 'demo', 'call'] heard="The best next step is a short demo. Tailored to your team's specific tools. I can set that up for this week. Did the call on Tuesday or Wednesday work? For you?"

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 42.3, 'error': None}.
