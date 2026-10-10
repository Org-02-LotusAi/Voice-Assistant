# Latency evidence (SOW section 20, TEST 11)

Identity `ava-001`, session `68b4dcacbc814c118326e2d4b0e6fd67`, 8 turns, host-measured (milliseconds, nearest-rank).

| Interval | n | P50 | P95 | MAX |
|---|---|---|---|---|
| T0->T4 (end of speech -> agent audio published) | 8 | 31,906 | 66,859 | 66,859 |
| T0->T1 (end of speech -> final transcript) | 8 | 531 | 2,719 | 2,719 |
| T1->T2 (transcript -> first LLM chunk) | 8 | 500 | 10,781 | 10,781 |
| T2->T3 (first LLM chunk -> first TTS PCM) | 8 | 25,516 | 63,218 | 63,218 |
| T3->T4 (first TTS PCM -> audio published) | 8 | 0 | 32 | 32 |
| T4->T5 (published -> heard by the human) | 8 | 219 | 937 | 937 |

SOW targets: P50 T0->T4 <= 700 ms, P95 <= 1500 ms. Ceiling met: **NO**.
Measured bottleneck (largest P50 interval): **t2_t3**.

## Cold-start check
- First turn T0->T4: 31,906 ms; later turns median: 37,719 ms.
- Engine at report time: warm=True, device=cpu, variant=turbo, cached_profiles=['1ae6b96a-8410-4455-af19-7c58bcdfbd94', '6326829e-38ae-475f-aedb-5c293aa43e51'].
- The runtime pre-warms the voice conditionals at job start and the engine warms the profile at launch preflight, so no avoidable cold start sits in these turns.

## Per-turn T0->T4 (ms)

#0: 31,906, #1: 16,625, #2: 15,765, #3: 39,219, #4: 52,516, #5: 37,719, #6: 24,469, #7: 66,859
