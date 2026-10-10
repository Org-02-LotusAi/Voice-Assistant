# TEST 15 - Mandatory Fresh-Learning Test (SOW 24): PASS

Run against the live Groq LLM (qwen/qwen3.8-27b) with the full manifest corpus ingested (12 sources) into a separate temp DB. Raw data: `expert_fresh_learning.json`.

## Fact (not in the original SOW/Template)
"LiveKit Agents worker stops accepting dispatches under host CPU load": on livekit-agents 1.8.3 the AgentServer marks the worker unavailable above load_threshold (default 0.7); even load_fnc=0.0 does not disable the gate (0.7 per reserved slot). Fix: AgentServer(load_threshold=math.inf) plus SERVICE015_MAX_ACTIVE_IDENTITIES cap (default 4, 409 capacity_exceeded).

## Provenance
- Ingested as verified_experience id 235 (POST /expert/experience, admin), min_role viewer
- repo VoiceSOW, branch main, sha 6faa3136ff5d226756a73846e4107d6d82aca4c5 (clean tree)
- source: src/service015/runtime/worker.py; .venv livekit/agents/worker.py (_is_available, load_threshold); evidence: task-9 live runs

## Question
"A second Identity launch sits in STARTING and the runtime worker never receives the dispatch while the TTS engine is busy on the same host. What causes this and what setting does Service 015 use to prevent it?"

## Retrieval
Rank of row 235 for the question (viewer role): **1** (of top 6).

## Before ingestion (fresh session dd4087d5..., control)
Expert said it had no knowledge of the mechanism; mentioned only SERVICE015_MAX_ACTIVE_IDENTITIES from the README. Neither "load_threshold" nor "0.7" appeared. No verified_experience citation.

## After ingestion (new session 87efcffe..., new app instance)
Answer: "The cause is the LiveKit Agents worker load gate. On livekit-agents 1.8.3, the AgentServer marks the worker unavailable when its load exceeds `load_threshold` (default 0.7)... The fix used in Service 015 is `AgentServer(load_threshold=math.inf)` plus an explicit `SERVICE015_MAX_ACTIVE_IDENTITIES` cap (default 4)... 409 `capacity_exceeded`."
Citations: verified_experience #235 (VoiceSOW/main/6faa3136ff5d), document README.md part 15/16.
Expectations: load_threshold = true, 0.7 = true; cites row 235 = true.

## Verdict: PASS
Note: ingest reported missing_required `evidence/latency*.json` (no such file at run time); unrelated to this test.
