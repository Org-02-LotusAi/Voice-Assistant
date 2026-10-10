# Acceptance run 20261010T115054Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | ten_turn | TEST 9 | **PASS** | 1247 s | - |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| ten_turn | 78.3/100.0 | 72/1201.7 | 11169 | 397.5 | 2167.2 | 1537.7 |

## Checks

### ten_turn (PASS)
- PASS: >= 10 turns completed -- 10
- PASS: turn_01: reply audio produced -- wait 15.2s
- PASS: turn_01: reply transcribed (non-empty) -- We help streamline your account. Management processes to save time and improve customer relationships. Could you share a bit about your current? Challenges so I can see if we're a good fit,
- PASS: turn_01: LLM text non-empty -- We help streamline your account management processes to save time and improve customer relationships. Could you share a bit about your current challenges so I can see if we’re a good fit?
- PASS: turn_01: relevant (keyword hit) -- hits=['help'] heard="We help streamline your account. Management processes to save time and improve customer relationships. Could you share a bit about your current? Challenges so I can see if we're a good fit,"
- PASS: turn_02: reply audio produced -- wait 18.72s
- PASS: turn_02: reply transcribed (non-empty) -- We usually work with mid-size teams in. B2b service industries who need to scale support without adding headcount. Does that sound like your organization?
- PASS: turn_02: LLM text non-empty -- We usually work with mid-sized teams in B2B service industries who need to scale support without adding headcount. Does that sound like your organization?
- PASS: turn_02: relevant (keyword hit) -- hits=['team', 'organi', 'industr'] heard='We usually work with mid-size teams in. B2b service industries who need to scale support without adding headcount. Does that sound like your organization?'
- PASS: turn_03: reply audio produced -- wait 18.76s
- PASS: turn_03: reply transcribed (non-empty) -- Typical onboarding ranges from two, two, Four weeks, depending on your team size and integration needs. I can connect you with our. Implementation lead to map out a specific timeline if you're interested.
- PASS: turn_03: LLM text non-empty -- Typical onboarding ranges from two to four weeks depending on your team size and integration needs. I can connect you with our implementation lead to map out a specific timeline if you're interested.
- PASS: turn_03: relevant (keyword hit) -- hits=['week', 'onboard', 'timeline', 'depend'] heard="Typical onboarding ranges from two, two, Four weeks, depending on your team size and integration needs. I can connect you with our. Implementation lead to map out a specific timeline if you're interested."
- PASS: turn_04: reply audio produced -- wait 19.08s
- PASS: turn_04: reply transcribed (non-empty) -- We integrate with most major Crm and, Support platforms though compatibility depends on your specific stack. Could you share which tools you rely on most? I can have our technical team confirm. Exact integrations in writing.
- PASS: turn_04: LLM text non-empty -- We integrate with most major CRM and support platforms, though compatibility depends on your specific stack. Could you share which tools you rely on most? I can have our technical team confirm exact integrations in writing.
- PASS: turn_04: relevant (keyword hit) -- hits=['integrat', 'tool', 'crm', 'stack', 'depend', 'confirm'] heard='We integrate with most major Crm and, Support platforms though compatibility depends on your specific stack. Could you share which tools you rely on most? I can have our technical team confirm. Exact integrations in writing.'
- PASS: turn_05: reply audio produced -- wait 12.83s
- PASS: turn_05: reply transcribed (non-empty) -- for small teams. Onboarding is typically a streamlined two-week process focused on core setup and a few key workflows. We start with a brief discovery call, too. Define priorities, then provide hands-on sessions with our team. Would you like me to connect you with? Are onboarding lead to review a sample schedule?
- PASS: turn_05: LLM text non-empty -- For small teams, onboarding is typically a streamlined two-week process focused on core setup and a few key workflows. We start with a brief discovery call to define priorities, then provide hands-on sessions with our team. Would you like me to connect you with our onboarding lead to review a sample schedule?
- PASS: turn_05: relevant (keyword hit) -- hits=['onboard', 'team', 'small', 'setup', 'start'] heard='for small teams. Onboarding is typically a streamlined two-week process focused on core setup and a few key workflows. We start with a brief discovery call, too. Define priorities, then provide hands-on sessions with our team. Would you like me to connect you with? Are onboarding lead to review a sample schedule?'
- PASS: turn_06: reply audio produced -- wait 17.66s
- PASS: turn_06: reply transcribed (non-empty) -- We follow strict data protection. Standards and encrypt all customer information at rest and in transit. I can have our security team send you. Our detailed compliance documentation for review Would that be helpful?
- PASS: turn_06: LLM text non-empty -- We follow strict data protection standards and encrypt all customer information at rest and in transit. I can have our security team send you our detailed compliance documentation for review. Would that be helpful?
- PASS: turn_06: relevant (keyword hit) -- hits=['secur', 'data', 'protect', 'encrypt', 'complian'] heard='We follow strict data protection. Standards and encrypt all customer information at rest and in transit. I can have our security team send you. Our detailed compliance documentation for review Would that be helpful?'
- PASS: turn_07: reply audio produced -- wait 15.88s
- PASS: turn_07: reply transcribed (non-empty) -- We don't offer free trials. But we do provide a tailored demo to show you exactly how we'd solve your specific challenges. I can set that up for you next week. Would later this week work?
- PASS: turn_07: LLM text non-empty -- We don’t offer free trials, but we do provide a tailored demo to show you exactly how we’d solve your specific challenges. I can set that up for you next week. Would later this week work?
- PASS: turn_07: relevant (keyword hit) -- hits=['trial', 'demo'] heard="We don't offer free trials. But we do provide a tailored demo to show you exactly how we'd solve your specific challenges. I can set that up for you next week. Would later this week work?"
- PASS: turn_08: reply audio produced -- wait 55.41s
- PASS: turn_08: reply transcribed (non-empty) -- Weep, purple. I dedicated account management. And 24/7 technical support to ensure you always have a direct line to help. I can connect you with our support lead To discuss specific Slas during our demo. Would that work for you?
- PASS: turn_08: LLM text non-empty -- We provide dedicated account management and 24/7 technical support to ensure you always have a direct line to help. I can connect you with our support lead to discuss specific SLAs during our demo. Would that work for you?
- PASS: turn_08: relevant (keyword hit) -- hits=['support', 'help'] heard='Weep, purple. I dedicated account management. And 24/7 technical support to ensure you always have a direct line to help. I can connect you with our support lead To discuss specific Slas during our demo. Would that work for you?'
- PASS: turn_09: reply audio produced -- wait 31.83s
- PASS: turn_09: reply transcribed (non-empty) -- Pricing is customized based on team size. and the specific integrations you need so I can't give an exact figure just yet I'll have our team send you a written. Proposal with the precise costs. Would you like me to get that started?
- PASS: turn_09: LLM text non-empty -- Pricing is customized based on team size and the specific integrations you need, so I can’t give an exact figure just yet. I’ll have our team send you a written proposal with the precise costs. Would you like me to get that started?
- PASS: turn_09: relevant (keyword hit) -- hits=['pric', 'cost', 'written'] heard="Pricing is customized based on team size. and the specific integrations you need so I can't give an exact figure just yet I'll have our team send you a written. Proposal with the precise costs. Would you like me to get that started?"
- PASS: turn_10: reply audio produced -- wait 19.59s
- PASS: turn_10: reply transcribed (non-empty) -- The best next step is a short, Tailored demo to show how we solve your specific challenges. I'll have my team send a calendar link. For a time that works for you. Does that sound good?
- PASS: turn_10: LLM text non-empty -- The best next step is a short, tailored demo to show how we solve your specific challenges. I’ll have my team send a calendar link for a time that works for you. Does that sound good?
- PASS: turn_10: relevant (keyword hit) -- hits=['next step', 'demo', 'calendar', 'send', 'time'] heard="The best next step is a short, Tailored demo to show how we solve your specific challenges. I'll have my team send a calendar link. For a time that works for you. Does that sound good?"

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 42.3, 'error': None}.
