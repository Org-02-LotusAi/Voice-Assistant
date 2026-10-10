# Latency evidence (SOW section 20, TEST 11)

Identity `ava-001`, session `34d59082195f4511b7217d64605776ca`, 13 turns, host-measured (milliseconds, nearest-rank).

| Interval | n | P50 | P95 | MAX |
|---|---|---|---|---|
| T0->T4 (end of speech -> agent audio published) | 13 | 18,188 | 20,750 | 20,750 |
| T0->T1 (end of speech -> final transcript) | 13 | 1,000 | 2,187 | 2,187 |
| T1->T2 (transcript -> first LLM chunk) | 13 | 297 | 969 | 969 |
| T2->T3 (first LLM chunk -> first TTS PCM) | 13 | 16,562 | 19,343 | 19,343 |
| T3->T4 (first TTS PCM -> audio published) | 13 | 0 | 78 | 78 |
| T4->T5 (published -> heard by the human) | 13 | 188 | 344 | 344 |

SOW targets: P50 T0->T4 <= 700 ms, P95 <= 1500 ms. Ceiling met: **NO**.
Measured bottleneck (largest P50 interval): **t2_t3**.

## Cold-start check
- First turn T0->T4: 16,078 ms; later turns median: 18,391 ms.
- Engine at report time: warm=True, device=cpu, variant=turbo, cached_profiles=['1ae6b96a-8410-4455-af19-7c58bcdfbd94', '6326829e-38ae-475f-aedb-5c293aa43e51'].
- The runtime pre-warms the voice conditionals at job start and the engine warms the profile at launch preflight, so no avoidable cold start sits in these turns.

## Per-turn T0->T4 (ms)

#0: 16,078, #1: 13,422, #2: 19,796, #3: 19,531, #4: 15,641, #5: 18,656, #6: 20,750, #7: 13,062, #8: 17,625, #9: 18,391, #10: 18,188, #11: 18,078, #12: 20,329
