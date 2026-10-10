# Latency evidence (SOW section 20, TEST 11)

Identity `ava-001`, session `cb27116283ae40068185027af7788d9a`, 12 turns, host-measured (milliseconds, nearest-rank).

| Interval | n | P50 | P95 | MAX |
|---|---|---|---|---|
| T0->T4 (end of speech -> agent audio published) | 12 | 34,141 | 90,687 | 90,687 |
| T0->T1 (end of speech -> final transcript) | 12 | 2,531 | 210,687 | 210,687 |
| T1->T2 (transcript -> first LLM chunk) | 12 | 469 | 33,094 | 33,094 |
| T2->T3 (first LLM chunk -> first TTS PCM) | 12 | 24,578 | 77,609 | 77,609 |
| T3->T4 (first TTS PCM -> audio published) | 12 | 0 | 16 | 16 |
| T4->T5 (published -> heard by the human) | 12 | 390 | 13,281 | 13,281 |

SOW targets: P50 T0->T4 <= 700 ms, P95 <= 1500 ms. Ceiling met: **NO**.
Measured bottleneck (largest P50 interval): **t2_t3**.

## Cold-start check
- First turn T0->T4: 11,328 ms; later turns median: 35,797 ms.
- Engine at report time: warm=True, device=cpu, variant=turbo, cached_profiles=['1ae6b96a-8410-4455-af19-7c58bcdfbd94', '6326829e-38ae-475f-aedb-5c293aa43e51'].
- The runtime pre-warms the voice conditionals at job start and the engine warms the profile at launch preflight, so no avoidable cold start sits in these turns.

## Per-turn T0->T4 (ms)

#0: 11,328, #1: 18,922, #2: 16,485, #3: 59,782, #4: 22,875, #5: 18,328, #6: 52,672, #7: 64,203, #8: 90,687, #9: 34,141, #10: 35,797, #11: 80,406
