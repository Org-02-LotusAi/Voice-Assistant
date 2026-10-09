# Phone dogfood: TEST 12 (real Service 014 + Exotel)

**Status: BLOCKED (external).** It needs a real Service 014 deployment, an Exotel number, and a LiveKit SIP
trunk, none of which exist yet. Everything below the adapter is covered by mocked tests only
(`tests/test_telephony.py`); no real call has been placed.

Service 015 does not implement SIP or PSTN. It reaches a phone only through Service 014 and the Identity's
Default LiveKit Room.

## Backends (`SERVICE014_MODE`)

| Mode | What Service 015 does | Status of the contract |
|---|---|---|
| `disabled` (default) | `POST .../phone/dial` answers 503 `telephony_not_configured` | n/a |
| `livekit_sip` | LiveKit `CreateSIPParticipant` on the shared LiveKit deployment: `sip_trunk_id=SERVICE014_SIP_TRUNK_ID`, `sip_call_to=<E.164>`, `room_name=<default_room>`, `participant_identity="phone-<last4>"`, `wait_until_answered=True`. Verified against `livekit-api==1.2.1` field names. | Real LiveKit API |
| `http` | `POST {SERVICE014_URL}/calls` with JSON `{"room": <default_room>, "to": <E.164>}`, header `Authorization: Bearer <SERVICE014_API_KEY>`; expects 2xx JSON `{"call_id": str, "status"?: str}` | **ASSUMED.** The real Service 014 HTTP contract is unknown. The assumption lives only in `HttpBackend` (`src/service015/telephony/service014.py`); change that one class once the real contract is known. |

Inbound: in `livekit_sip` mode, `GET /identities/{id}/phone` reports `inbound = {kind: SIPDispatchRuleDirect,
room_name: <default_room>}`. Service 015 does not create the rule; Service 014 must configure a LiveKit
`SIPDispatchRuleDirect(room_name=<default_room>)` (plus the `livekit/sip` service, shared Redis and a public
IP; `livekit-server --dev` alone cannot do SIP).

Failure mapping: Service 014/SIP failure -> 502 `telephony_failed` (or `telephony_bad_response` when the http
reply has no `call_id`); missing config (`SERVICE014_URL` or `SERVICE014_SIP_TRUNK_ID`) -> 503
`telephony_not_configured`. The phone number is logged masked (last 4 only) and never appears in errors.

## Prerequisites for TEST 12

1. Service 014 reachable, with an Exotel number and an outbound SIP trunk on the same LiveKit deployment
   that Service 015 uses (`LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`). Note the trunk id (`ST_...`).
2. `.env` for Service 015 (never commit): `SERVICE014_MODE=livekit_sip` and `SERVICE014_SIP_TRUNK_ID=ST_...`
   (or `SERVICE014_MODE=http`, `SERVICE014_URL`, `SERVICE014_API_KEY` if Service 014 exposes `/calls`).
3. The Identity runtime (Task 6+) working: an identity can be launched and reaches presence `READY`.
4. A real phone you control, with its E.164 number (for example `+919876543210`).
5. An operator API key (`SERVICE015_API_KEYS`); `$K` below.

## Procedure

1. Start LiveKit (with SIP), Voicebox, and Service 015 (`uv run service015-api`).
2. Create an identity if needed:
   `POST /templates/sales-representative/instantiate {"overrides":{"name":"Ava","company":"Safi"}}` (id `ava-001`).
3. Check config: `GET /identities/ava-001/phone` -> `mode` is `livekit_sip` (or `http`) and `default_room` is
   `ava-001`.
4. Negative check: while the identity is `OFFLINE`, `POST /identities/ava-001/phone/dial {"to":"+91..."}`
   must return 409 `identity_not_ready`.
5. `POST /identities/ava-001/launch`; poll `GET /identities/ava-001/status` until `presence` is `READY`.
6. `curl -X POST http://localhost:8015/identities/ava-001/phone/dial -H "Authorization: Bearer $K" -H "Content-Type: application/json" -d '{"to":"+91XXXXXXXXXX"}'`
   -> expect 202 `{"call_id": "...", "room": "ava-001", "status": "answered"}` (`livekit_sip` returns only after
   the phone is answered; allow up to about a minute).
7. Answer the phone. Speak a sentence; the Identity must respond in its voice. Interrupt it mid-reply to confirm
   barge-in. Confirm in LiveKit that participant `phone-<last4>` is in room `ava-001` alongside the Identity.
8. `GET /identities/ava-001/status` shows `BUSY` during the call (when the runtime sets it).
9. Hang up from the phone; confirm the SIP participant leaves the room and the Identity returns to `READY`.
10. Inbound (if configured): call the Exotel number; it should land in `ava-001` via the dispatch rule and the
    Identity answers.
11. Check Service 015 logs: the number appears only masked (`*********3210`).

## Pass criteria

- Step 4 returns 409; step 6 returns 202 with a `call_id`.
- Two-way audio with the Identity's voice, barge-in works, no echo loop.
- Hang-up cleans up the SIP participant; the number is never logged in full.

Record the outcome, date, and first-audio latency here once run. Until then TEST 12 remains BLOCKED (external).
