# Acceptance run 20261010T095815Z

Host: win32, python 3.12.13. Engine: turbo on cpu. LLM: qwen/qwen3.8-27b.

| # | Scenario | SOW | Status | Duration | Failed checks |
|---|---|---|---|---|---|
| 1 | failure_recovery | SOW 27 | **PASS** | 334 s | - |

## Host load per scenario (psutil, every 2 s; raw samples in resources.jsonl)

| Scenario | CPU % avg/max | RAM available MB min/avg | swap used MB max | engine CPU% avg | engine RSS MB max | worker+jobs RSS MB max |
|---|---|---|---|---|---|---|
| failure_recovery | 63.2/96.4 | 42/1998.0 | 10315 | 130.7 | 5574.6 | 1495.5 |

## Checks

### failure_recovery (PASS)
- PASS: invalid override (disallowed override key) -> 422 -- 422 override_not_allowed
- PASS: invalid override (non-boolean barge_in) -> 422 -- 422 invalid_override
- PASS: invalid override (empty name) -> 422 -- 422 invalid_override
- PASS: invalid override (missing required company) -> 422 -- 422 missing_required_field
- PASS: identity unchanged after invalid overrides
- PASS: no identity was created by rejected instantiates
- PASS: presence became ERROR after the user's turn -- llm_unavailable | llm error: LLMError: type='llm_error' timestamp=1791626379.063495 label='livekit.plugins.openai.llm.LLM' error=APIConnectionError('Connection error.', body=None, retryable=True) recoverable=False
- PASS: error is llm_unavailable -- llm_unavailable | llm error: LLMError: type='llm_error' timestamp=1791626379.063495 label='livekit.plugins.openai.llm.LLM' error=APIConnectionError('Connection error.', body=None, retryable=True) recoverable=False
- PASS: no speech after the user's turn (no fabricated reply) -- 0.00s of audio above the RMS gate
- PASS: launch with the engine down -> 503 -- 503
- PASS: error names the dependency failure (voice_engine) -- {"code": "dependency_unavailable", "message": "the voice engine is not healthy (status: unavailable)", "details": {"dependency": "voice_engine", "engine_status": "unavailable"}}
- PASS: identity unchanged by the failed launch
- PASS: presence still OFFLINE after the failed launch -- OFFLINE
- PASS: engine restored and warm -- 180.1s
- PASS: relaunch after restore reaches READY -- 48.2s
- PASS: identity unchanged after recovery
- PASS: engine still up and warm at the end -- {"status": "ok", "engine": "chatterbox-turbo", "variant": "turbo", "device": "cpu", "sample_rate": 24000, "warm": true, "cached_profiles": ["6326829e-38ae-475f-aedb-5c293aa43e51"], "nano_supported": true, "load_seconds": 42.3, "error": null}

## Notes

- Timeouts/thresholds used: rms_threshold=300.0, launch_timeout_s=240.0, greeting_timeout_s=240.0, reply_start_timeout_s=120.0, reply_complete_timeout_s=240.0, post_interrupt_reply_timeout_s=360.0, settle_s=2.0, quiet_gap_s=1.0, barge_in_after_s=1.5, barge_in_limit_ms=1000.0, p50_limit_ms=700.0, p95_limit_ms=1500.0, engine_restore_timeout_s=900.0, llm_error_timeout_s=120.0, ten_turn_count=10.
- FAIL-ON-THIS-HOST means the SOW latency ceiling was missed on this CPU-only host; the measured bottleneck is in latency.md.
- Leftover processes after teardown: none.
- Engine after the run: {'status': 'ok', 'engine': 'chatterbox-turbo', 'variant': 'turbo', 'device': 'cpu', 'sample_rate': 24000, 'warm': True, 'cached_profiles': ['6326829e-38ae-475f-aedb-5c293aa43e51'], 'nano_supported': True, 'load_seconds': 42.3, 'error': None}.
