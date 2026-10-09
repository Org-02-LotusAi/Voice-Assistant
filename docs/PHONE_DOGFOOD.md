# Phone dogfood: TEST 12 (real Service 014 + Exotel)

**Status: BLOCKED (external).** It needs a real Service 014 deployment, an Exotel number and a LiveKit SIP
trunk, none of which exist yet. Everything below the adapter is covered by mocked tests (`tests/test_telephony.py`);
no real call has been placed. Commands in this runbook that touch only LiveKit (`scripts/phone_ops.py participants`)
were exercised against the local dev LiveKit; the SIP calls were NOT (marked "unverified").

Service 015 does not implement SIP or PSTN. It reaches a phone only through Service 014 and the Identity's
Default LiveKit Room.

## Backends (`SERVICE014_MODE`)

| Mode | What Service 015 does | Status of the contract |
|---|---|---|
| `disabled` (default) | `POST .../phone/dial` answers 503 `telephony_not_configured` | n/a |
| `livekit_sip` | LiveKit `CreateSIPParticipant` on the shared LiveKit deployment: `sip_trunk_id=SERVICE014_SIP_TRUNK_ID`, `sip_call_to=<E.164>`, `room_name=<default_room>`, `participant_identity="phone-<last4>-<rand4>"` (unique per call), `wait_until_answered=True`, `ringing_timeout=45 s`. Field names verified against `livekit-api==1.2.1`. | Real LiveKit API |
| `http` | `POST {SERVICE014_URL}/calls` with JSON `{"room": <default_room>, "to": <E.164>}`, header `Authorization: Bearer <SERVICE014_API_KEY>`; expects 2xx JSON `{"call_id": str, "status"?: str}`. An empty `SERVICE014_API_KEY` is a configuration error (503 `telephony_not_configured`), never an unauthenticated call. | **ASSUMED.** The real Service 014 HTTP contract is unknown. The assumption lives only in `HttpBackend` (`src/service015/telephony/service014.py`); change that one class once the real contract is known. |

**The dial request is synchronous.** In `livekit_sip` mode it returns only when the callee answers or the 45 s ringing
timeout expires (then 502 `telephony_failed`); `http` mode blocks until Service 014 replies (up to 60 s). Use a client
timeout of at least 70 s (`curl --max-time 70`). Success is `202 {"call_id","room","status"}`.

Failure mapping: no answer / busy / SIP rejection / Service 014 down -> 502 `telephony_failed` (or
`telephony_bad_response` when the http reply has no `call_id`); missing config (`SERVICE014_URL`,
`SERVICE014_API_KEY` in http mode, `SERVICE014_SIP_TRUNK_ID` in livekit_sip mode) -> 503 `telephony_not_configured`.
Service 015 deliberately hides upstream error text and the full number; the cause is in the LiveKit server and
`livekit/sip` logs (`docker compose logs sip livekit`) or in Service 014's logs. Numbers appear in Service 015 logs
masked (last 4 digits only).

## Inbound (owned by Service 014)

`GET /identities/{id}/phone` reports `inbound = {kind: "SIPDispatchRuleDirect", room_name: <default_room>}` in
`livekit_sip` mode. Service 015 does not create the rule. Service 014 needs a LiveKit inbound trunk (for the Exotel
number) and, per Identity, a direct dispatch rule. The exact call Service 014 would make (livekit-api 1.2.1; unverified
against a SIP-enabled server):

```python
from livekit import api
rule = api.CreateSIPDispatchRuleRequest(
    rule=api.SIPDispatchRule(dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name="ava-001")),
    trunk_ids=["ST_inbound_trunk_id"],
    name="ava-001 inbound",
)
info = await lk.sip.create_sip_dispatch_rule(rule)   # lk = api.LiveKitAPI(url, key, secret)
```

The same call from this repo: `uv run python scripts/phone_ops.py dispatch-direct ava-001 --trunk-id ST_inbound_trunk_id`.
The equivalent LiveKit CLI form is `lk sip dispatch create` with a JSON body containing
`{"dispatch_rule": {"rule": {"dispatchRuleDirect": {"roomName": "ava-001"}}, "trunk_ids": ["ST_..."]}}`
(CLI flags unverified; prefer the Python call above). `livekit-server --dev` alone cannot do SIP: run the `livekit/sip`
service (compose `sip` profile: `docker compose --profile sip up -d sip`) with the shared Redis and a public IP.

## Prerequisites

1. Service 014 reachable, with an Exotel number and an outbound SIP trunk on the same LiveKit deployment that Service
   015 uses (`LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`). Note the outbound trunk id (`ST_...`).
2. `.env` for Service 015 (never commit): `SERVICE014_MODE=livekit_sip` and `SERVICE014_SIP_TRUNK_ID=ST_...`
   (or `SERVICE014_MODE=http`, `SERVICE014_URL`, `SERVICE014_API_KEY` if Service 014 exposes `/calls`).
3. `LLM_API_KEY` set (the Identity answers through the LLM).
4. A real phone you control and its E.164 number (for example `+919876543210`).
5. Keys: `K` = an operator key from `SERVICE015_API_KEYS`.

## Procedure (every step is a command)

Run the first four blocks in separate terminals (repo root). Windows shown first, Linux/Git Bash second where they differ.

1. **Dependencies** (LiveKit in Docker, Voicebox headless on 17493; add `-Warm` / `WARM=1` the first time to download and load the 3.9 GB Chatterbox Turbo weights):
   ```
   powershell -File scripts/run_deps.ps1 -Action start -Warm        # Linux: WARM=1 scripts/run_deps.sh start
   ```
   LiveKit with SIP instead of the dev server: `docker compose --profile sip up -d livekit redis sip` (compose stack, see README).
2. **Voice engine** (port 17494; wait until `status` is `ok`, a minute or more on CPU, much faster on GPU):
   ```
   powershell -File scripts/run_engine.ps1 -Action start            # Linux: scripts/run_engine.sh start
   curl http://127.0.0.1:17494/health
   ```
3. **Seed voices and authorize them** (once per fresh Voicebox/DB):
   ```
   uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py
   uv run python scripts/authorize_seed_voices.py
   ```
4. **API with the LiveKit supervisor** (`LIVEKIT_URL/KEY/SECRET` explicitly set in `.env` makes `create_app` use
   `LiveKitSupervisor`, which spawns the runtime worker `python -m service015.runtime.worker`, agent name
   `service015-identity`; without them launch answers 503). Check `runtime: ok` in `/health`:
   ```
   uv run service015-api
   curl http://localhost:8015/health
   ```
5. **Create and launch the Identity** (POSIX shell; `K=<operator key>`):
   ```
   curl -s -X POST http://localhost:8015/templates/sales-representative/instantiate -H "Authorization: Bearer $K" \
     -H "Content-Type: application/json" \
     -d '{"overrides":{"name":"Ava","company":"Safi","voice.profile_id":"<Ava Warm profile id from seed_voicebox_profiles.py>"}}'
   curl -s http://localhost:8015/identities/ava-001/phone -H "Authorization: Bearer $K"     # mode livekit_sip, default_room ava-001
   ```
6. **Negative check** (identity is OFFLINE): must be 409 `identity_not_ready`:
   ```
   curl -s -w "\n%{http_code}\n" -X POST http://localhost:8015/identities/ava-001/phone/dial -H "Authorization: Bearer $K" \
     -H "Content-Type: application/json" -d '{"to":"+91XXXXXXXXXX"}'
   ```
7. **Launch and wait for READY** (the runtime loads Moonshine + Silero on first launch; on a CPU host expect ~25 s, GPU less):
   ```
   curl -s -X POST http://localhost:8015/identities/ava-001/launch -H "Authorization: Bearer $K"
   until curl -s http://localhost:8015/identities/ava-001/status -H "Authorization: Bearer $K" | grep -q '"presence":"READY"'; do sleep 2; done; echo READY
   ```
   Presence is `READY` while the agent waits alone and `BUSY` while a human or caller is in the room.
8. **Dial** (blocks until answered or up to 45 s ringing; keep the phone ready):
   ```
   curl -s --max-time 70 -w "\n%{http_code}\n" -X POST http://localhost:8015/identities/ava-001/phone/dial \
     -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"to":"+91XXXXXXXXXX"}'
   ```
   Expected: `202 {"call_id":"...","room":"ava-001","status":"answered"}`.
   **Outcomes if not**: nobody answers within 45 s, busy, or the trunk rejects -> `502 telephony_failed`
   (identity unaffected, still READY, retry allowed; see the LiveKit/SIP logs for the SIP status code);
   identity not READY/BUSY -> 409 `identity_not_ready`; trunk id / url missing -> 503 `telephony_not_configured`;
   number not E.164 -> 422.
9. **Verify the room** (the SIP participant is `phone-<last4>-<rand4>`, kind SIP, next to the agent `ava-001`, kind AGENT):
   ```
   uv run python scripts/phone_ops.py participants ava-001
   curl -s http://localhost:8015/identities/ava-001/status -H "Authorization: Bearer $K"     # presence BUSY while the caller is in
   ```
10. **Talk.** Speak a sentence; the Identity must answer in its voice. Interrupt it mid-reply to confirm barge-in.
    Listen for an echo loop (there should be none: the PSTN leg is a separate participant).
11. **Hang up** from the phone. Re-run the participants command: the SIP participant is gone. Presence returns to `READY`:
    `curl -s http://localhost:8015/identities/ava-001/status -H "Authorization: Bearer $K"`.
12. **Inbound** (if Service 014 created the dispatch rule above): call the Exotel number; it lands in `ava-001`,
    the Identity greets/answers. Verify with the participants command.
13. **Cleanup**: stop the identity and remove a lingering SIP leg if hang-up did not clear it:
    ```
    uv run python scripts/phone_ops.py remove ava-001 phone-3210-ab12     # only if still listed (use the real identity string)
    curl -s -X POST http://localhost:8015/identities/ava-001/stop -H "Authorization: Bearer $K"
    ```
    Stop the stack: Ctrl+C the API (this stops the worker), `scripts/run_engine.* -Action stop`, `scripts/run_deps.* -Action stop`.
14. **Logs**: Service 015 logs show the number only masked (`*********3210`), e.g. `grep 'phone call placed' <api output>`.

## Pass criteria

- Step 6 returns 409; step 8 returns 202 with a `call_id`.
- Step 9 lists a `kind=SIP` participant in room `ava-001` alongside the agent.
- Two-way audio with the Identity's voice, barge-in works, no echo loop.
- Hang-up cleans up the SIP participant and presence returns to READY; the number is never logged in full.
- Measure first-audio latency by wall clock from the end of your sentence to the first audible reply and record it below;
  `GET /identities/ava-001/latency` reports the T0-T4 server-side numbers (T5, the audible leg, is not measured over SIP).

Record the outcome, date and first-audio latency here once run. Until then TEST 12 remains BLOCKED (external).
