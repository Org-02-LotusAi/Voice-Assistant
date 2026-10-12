# Integrating the Meeting Manager platform with Service 015

Audience: the team building the Meeting Manager / platform that puts AI voice participants into meetings.
The contract is `openapi.yaml` (also served at `GET /openapi.yaml`); this guide is a walkthrough. All examples use
the field names in that file. Base URL below is `http://localhost:8015`; replace with the deployed (https) URL.

The one idea: an **Identity** is a persistent AI participant (`participant_identity == default_room == identity_id`,
e.g. `ava-001`, fixed forever). You create it once, launch it when needed, and meet it in its **Default Room** on
LiveKit. You never talk to speech models; you talk to a participant.

## 1. Authentication and roles

Every route except `GET /health`, the static UI, and `GET /openapi.yaml` needs `Authorization: Bearer <key>`.
Keys are configured on the server in `SERVICE015_API_KEYS="key1:admin,key2:operator,key3:viewer"`.

| Role | Can do (platform-relevant) |
|---|---|
| `viewer` | every GET (roster, status, identity, latency, phone info, templates), expert questions |
| `operator` (includes viewer) | instantiate, PATCH, launch, stop, join-token, phone dial |
| `admin` (includes both) | create shared templates/versions, register shared voices, record voice authorizations, ingest expert experience, `/admin/*` (users, service tokens, all-owner personas and voices), disable templates/voices, assign owners, `POST /expert/reingest` |

Admin-issued **service tokens** (`POST /admin/tokens`, secret shown once, revocable) carry role `viewer` or `operator` and are used exactly like env keys. Operator keys/tokens cannot author templates or voices (403).

**Machine tokens and cookies.** Integrations must use Bearer credentials: an env key, or a service token an administrator creates in the Admin console (`/admin.html` > Service tokens; pick role `viewer` for dashboards or `operator`; copy the secret at creation, it is shown once; revoke it there). Cookie sessions (`/login`, `s015_session` plus `X-CSRF-Token`; the CSRF token is returned by `POST /auth/login` and `GET /auth/me`) are for humans in a browser only; a Bearer header, when present, wins over any cookie. Human accounts scope what they see by owner (`user`) or are read-only (`viewer`); tokens and env keys keep Phase-1 semantics.

The platform backend should hold an **operator** key (and a viewer key for read-only dashboards). Keep keys server-side;
never ship them to browsers. 401 `unauthorized` = missing/unknown key, 403 `forbidden` = role too low.

```bash
export S015=http://localhost:8015
export K=<operator key>
```

## 2. Error model

Every non-2xx JSON body has the same shape:

```json
{"error": {"code": "identity_not_ready", "message": "identity presence is OFFLINE; it must be READY or BUSY to take a phone call", "details": {"presence": "OFFLINE", "required": ["READY", "BUSY"]}}}
```

Branch on `error.code` (stable, snake_case), show `message` to humans, read `details` for structured context.
Codes you will meet: `unauthorized` 401, `forbidden` 403, `identity_not_found` / `template_not_found` 404,
`invalid_request` / `override_not_allowed` / `invalid_override` / `missing_required_field` / `voice_required` /
`voice_profile_not_found` / `voice_not_authorized` 422, `capacity_exceeded` / `identity_not_ready` 409,
`voicebox_unavailable` / `dependency_unavailable` / `telephony_not_configured` 503, `telephony_failed` /
`voicebox_rejected` 502.

## 3. Walkthrough

### 3.1 Roster: who can I put in a meeting?

```bash
curl -s $S015/meeting/participants -H "Authorization: Bearer $K"
```
```json
[{"identity_id": "ava-001", "participant_identity": "ava-001", "default_room": "ava-001", "presence": "READY",
  "status_detail": null, "last_error": null,
  "capabilities": {"audio_receive": true, "audio_publish": true, "barge_in": true, "video_publish": false},
  "launched_at": "2026-10-10T09:00:00Z", "heartbeat_at": "2026-10-10T09:05:10Z"}]
```
One `ParticipantStatus` per Identity, ordered by `identity_id`. Metadata only: no speech, model or prompt internals.
Show only `READY` (or `BUSY`) ones as available; offer a "start" action for `OFFLINE` ones.

### 3.2 Create an Identity from a template

```bash
curl -s $S015/templates -H "Authorization: Bearer $K"                  # sales-representative, customer-support, generic-assistant
curl -s -X POST $S015/templates/sales-representative/instantiate -H "Authorization: Bearer $K" \
  -H "Content-Type: application/json" \
  -d '{"overrides": {"name": "Ava", "company": "Safi", "role": "Account Specialist", "voice.profile_id": "<voice id>"}, "template_version": 1}'
```
201 returns the resolved Identity (`identity_id: "ava-001"`, `participant_identity`, `default_room`, `resolved_hash`).
`template_version` is optional (default latest) and is then frozen: later template changes never alter this Identity.
Only keys in the template's `allowed_overrides` are accepted (422 `override_not_allowed`). `voice.profile_id` must be a
cloned voice that has an authorization record (`GET /voices` lists `voice_profile_id` and `authorized`); otherwise 422
`voice_not_authorized` / `voice_profile_not_found`. Required fields (`name`, `company` for sales) are 422
`missing_required_field` when absent.

### 3.3 Change an Identity

```bash
curl -s -X PATCH $S015/identities/ava-001 -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"overrides": {"role": "Senior Specialist", "conversation.barge_in": null}}'
```
`null` clears an override (reverts to the template value). The id, `participant_identity` and `default_room` never
change. The runtime loads the Identity from the database when it starts, so a running Identity picks the change up on its next launch (stop, then launch).

### 3.4 Launch, status, stop

```bash
curl -s -X POST $S015/identities/ava-001/launch -H "Authorization: Bearer $K"     # 200: ParticipantStatus, normally STARTING
curl -s $S015/identities/ava-001/status -H "Authorization: Bearer $K"             # poll
curl -s -X POST $S015/identities/ava-001/stop   -H "Authorization: Bearer $K"     # 200: OFFLINE
```
Launch is idempotent (launching a running Identity returns its status). Poll `status` every 1-2 s until
`presence` is `READY`; expect 10-30 s on first start (models load).

Launch preconditions (all 4xx/5xx carry `error.code`): the Identity needs a voice (422 `voice_required`) that still
exists and is authorized (422 `voice_profile_not_found` / `voice_not_authorized`); Voicebox down 503
`voicebox_unavailable`; no runtime configured 503 `dependency_unavailable`.

**Capacity.** At most `SERVICE015_MAX_ACTIVE_IDENTITIES` (default 2) Identities may be STARTING/READY/BUSY at once;
the next launch answers `409 capacity_exceeded` with `details: {"active": 4, "limit": 4}`. Stop one first (or raise the
limit with the hardware). Relaunching an already-running Identity is not counted twice.

### 3.5 Presence semantics (the contract with your UI)

| `presence` | Meaning | What the platform does |
|---|---|---|
| `OFFLINE` | not running | offer "start" |
| `STARTING` | launching, loading models, not yet in the room as ready | show spinner; poll |
| `READY` | in its room, healthy, nobody talking to it | can be added/joined/dialed |
| `BUSY` | in its room with at least one other participant (human or phone) | joinable (rooms are shared), show as in-call |
| `ERROR` | failed or went stale; `status_detail` and `last_error` say why | show error, offer stop / relaunch |

`status_detail` values to expect on ERROR: `heartbeat_lost` (no heartbeat for >15 s: process died or hung),
`worker_exited`, `start_timeout` (STARTING longer than 120 s), `livekit_disconnected`, `RECONNECTING` (transient),
`llm_unavailable`, `llm_rate_limited` (HTTP 429 from the LLM provider; the runtime backs off and recreates the session), `stt_failure`, `tts_unavailable`, `voice_required`. **Staleness is computed when you read**: a
`READY`/`BUSY` row whose `heartbeat_at` is older than 15 s is reported as `ERROR heartbeat_lost` even though nothing wrote
it, so always trust `presence`, not an old cached value, and treat `heartbeat_at` as informational. `launch` on an
`ERROR` or stale Identity is allowed and restarts it. `GET /meeting/participants` and `GET /identities/{id}/status`
apply the same staleness rules (a worker process death is only distinguished as `worker_exited` on `/status`).

### 3.6 Let a human into the room

```bash
curl -s -X POST $S015/identities/ava-001/join-token -H "Authorization: Bearer $K" \
  -H "Content-Type: application/json" -d '{"participant_name": "Sam"}'
```
```json
{"livekit_url": "wss://lk.example.com", "room": "ava-001", "token": "<JWT>", "participant_identity": "human-3f9a02c1"}
```
Give `livekit_url` + `token` to the LiveKit client SDK in the user's browser (`new Room().connect(livekit_url, token)`,
then publish the microphone and play the agent's audio track). The token is valid for 1 hour, grants publish and
subscribe in that one room, and the participant is `human-<random8>` (never the Identity's own identity). It works
whether or not the Identity is launched. Browsers need https for microphone access on any non-localhost origin.
`participant_name` is optional (display name).

### 3.7 Latency (for dashboards / SLOs)

```bash
curl -s "$S015/identities/ava-001/latency" -H "Authorization: Bearer $K"                 # all turns
curl -s "$S015/identities/ava-001/latency?session_id=<id>" -H "Authorization: Bearer $K" # one runtime session
```
Returns `turns` and `t0_t4`, `t0_t1`, `t1_t2`, `t2_t3`, `t3_t4`, `t4_t5`, each `{count, p50, p95, max}` in
milliseconds (nearest rank; `null` statistics when `count` is 0). T0 user stops speaking, T1 final transcript, T2 first
LLM chunk, T3 first audio generated, T4 agent starts speaking, T5 first audio heard by the human (only filled by the
e2e harness). The SOW target is P50 <= 700 ms and P95 <= 1500 ms for T0-T4 on the GPU reference host.
Intervals are raw and may occasionally be negative (the transcript can finalize before end-of-speech detection).

### 3.8 Phone: dial a number into the room

Only when the deployment has Service 014 wired in (`GET /identities/ava-001/phone` shows `mode` other than
`disabled`; otherwise dial answers 503 `telephony_not_configured`).

```bash
curl -s $S015/identities/ava-001/phone -H "Authorization: Bearer $K"
# {"mode":"livekit_sip","default_room":"ava-001","inbound":{"kind":"SIPDispatchRuleDirect","room_name":"ava-001"}}
curl -s --max-time 70 -X POST $S015/identities/ava-001/phone/dial -H "Authorization: Bearer $K" \
  -H "Content-Type: application/json" -d '{"to": "+919876543210"}'
# 202 {"call_id":"SCL_...","room":"ava-001","status":"answered"}
```
`to` must be E.164 (422 otherwise). The Identity must be `READY` or `BUSY` (409 `identity_not_ready` with
`details.presence` otherwise). The call is **synchronous**: it returns after the callee answers or after the ringing
timeout (45 s, then 502 `telephony_failed`), so use a client timeout of at least 70 s. The phone joins the same Default
Room as `phone-<last4>-<rand4>`, so everyone in the room (including humans who used a join token) hears both. Service
015 does not implement SIP; it asks Service 014. Inbound calls are routed by Service 014 into the Default Room.
See `docs/PHONE_DOGFOOD.md`.

## 4. Typical meeting flow

1. `GET /meeting/participants` to render the picker; `POST /templates/{id}/instantiate` if the user needs a new one.
2. User adds Ava to the meeting: `POST /identities/ava-001/launch`, poll status to `READY` (handle `409 capacity_exceeded`
   and ERROR states in the UI).
3. For each human joining the call: `POST /identities/ava-001/join-token`, connect with the LiveKit SDK.
   For phones: `POST /identities/ava-001/phone/dial`.
4. Meeting over: `POST /identities/ava-001/stop` (idempotent; it removes the participant from the room).

## 5. What the platform must NOT need to know (SOW section 19)

The platform sees participants, rooms, tokens and presence, nothing below that line. Specifically it does **not** need,
and the API does not expose in roster/status/join-token/dial:

- which TTS engine, voice cloning tool, STT model, or LLM provider/model is used, or their endpoints, keys or versions;
- prompts, persona text, runtime instructions, or how self-facts are enforced (the authoritative self-context is
  inspectable by any authenticated key via `GET /identities/{id}/self-context`, but it is a debugging view, not an integration surface);
- Voicebox profile internals or reference audio (voice ids are opaque; samples are never returned);
- how the runtime process is started, supervised or scaled (one worker serves all Identities);
- how audio is chunked, buffered or interrupted (barge-in works inside the participant).

If your integration needs one of these, that is a missing platform-level capability; ask for it in the contract
instead of reaching into internals.

## 6. Operational notes for integrators

- Run a single API process per database (launch/stop locking is per process).
- `GET /health` is unauthenticated and reports dependency states (`database`, `runtime`, `voicebox`, `voice_engine`);
  voice dependencies are informational and never fail the probe. Use it for load-balancer checks.
- Rooms persist while the Identity runs; a stopped Identity leaves its room.
- Time fields are ISO-8601 UTC.
