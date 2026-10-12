# Phase 2 live smoke

Run against the real data DB on localhost:8015 (real Voicebox, engine, LiveKit). Statuses only; no secrets recorded.

| Step | HTTP | Note |
|---|---|---|
| admin login | 200 |  |
| admin creates user demo | 201 |  |
| demo login (temp password) | 200 | must_change_password=True |
| demo GET /identities before change | 403 | password_change_required |
| demo forced password change | 200 |  |
| demo login (new password) | 200 |  |
| demo GET /templates | 200 | concierge, customer-support, generic-assistant, sales-representative |
| demo GET /voices | 200 | James Calm shared=True owned_by_me=False authorized=True; Ava Warm shared=True owned_by_me=False authorized=True |
| demo creates persona from shared template + shared voice | 201 | demo-ava-001 |
| demo launch | 200 | STARTING |
| demo status poll | 200 | presence=READY after 3s |
| viewer login | 200 |  |
| viewer GET /meeting/participants | 200 | sees demo persona: True presence=READY |
| viewer POST stop (must be refused) | 403 | forbidden |
| token GET /meeting/participants | 200 | 7 personas |
| token POST stop (must be 403) | 403 | forbidden |
| token POST instantiate (must be 403) | 403 | forbidden |
| demo stop | 200 | OFFLINE |
| demo archive | 200 | {"identity_id":"demo-ava-001","archived_at":"2026-10-12T11:07:01.122590+00:00"} |
| demo list after archive | 200 | 0 personas |
| viewer roster after archive | 200 | demo persona present: False |
| admin sees archived | 200 | archived_at set: True |
| admin disables demo account (cleanup) | 200 |  |
