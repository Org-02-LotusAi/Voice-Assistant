# Task P2 report: admin + owner management APIs

## Built
- `src/service015/api/routers/admin.py` (new): `GET/POST /admin/users`, `PATCH /admin/users/{id}`, `GET/POST /admin/tokens`, `DELETE /admin/tokens/{id}` (204), `GET /admin/identities?include_archived=`, `PATCH /admin/identities/{id}` (assign owner), `GET /admin/voices` (owner, shared, disabled, consent record; `VoiceProfile` left unchanged).
- `templates.py`: `POST /templates` now `operator` rank + author check (user = private, admin = shared); `PATCH /templates/{id}` (admin: disabled / shared / owner_user_id). Ownership check for versioning runs inside the write transaction (`templates_repo._insert_version(authorize=...)`), so concurrent creation of one id cannot cross owners.
- `voices.py`: `POST /voices` now `operator` rank + author check (user upload owned by uploader, consent fields mandatory); `PATCH /voices/{id}` (admin: disabled / owner). App middleware gate for `/voices` updated (still authenticates before the body is read).
- `expert.py`: `POST /expert/reingest` (admin, `ingest_manifest` from cwd); sessions scoped: migration `007_expert_session_owner.sql` adds `expert_sessions.owner_key` (`user:<id>` for human sessions, NULL for machine). Non-admin human asking in a foreign/machine session gets 404 `session_not_found`; admin accounts and env keys/tokens stay global (Phase 1 compatible).
- `selfcontext.py`: `GUARDRAILS` block (header `LOCKED GUARDRAILS`) appended by the builder after the persona fence and before `SPOKEN CONVERSATION RULES` for every identity; header added to the neutralized markers so persona text cannot forge it. Not stored in templates.
- `accounts.py`: `list_users`, `admin_create_user`, `admin_update_user`, `list_service_tokens`. `auth.py`: `ensure_author`. `authz.py`: `consent_index`, `set_voice_owner`. `identities_repo.py`: `set_owner`, `archived_map`.
- openapi.yaml (both copies byte-identical): 12 new operations/methods, new schemas, `admin` tag, authoring and expert-session notes.

## Decisions
- Service tokens: P1 already had the stub; kept `s015_` + token_urlsafe(32) secret, separate public `tok_<id>`, sha256 stored, secret only in the create response, list never shows it.
- Admin users: temporary password via the P1 policy -> `must_change_password`; disable / role change / reset revoke sessions immediately. Roles assignable: `user`, `viewer` only. Admin accounts cannot be created, promoted to, or modified via the API (409 `admin_account_protected`, no lock-out of the bootstrap admin).
- Authoring (templates, voices): human `user`/`admin` accounts and admin keys/tokens only; operator keys/tokens get 403 (Phase 1 admin-only behaviour preserved for machines). Spec says `x-required-role: operator`; contract test has an `AUTHORING` exception documenting this.
- A `template_id` that exists but is invisible to the caller returns 409 `template_id_taken` (no 404/403 leakage difference between "exists privately" and... it does reveal that the id is in use; acceptable and documented). Admin cannot version someone else's private template (403 `template_not_owned`), can disable/reassign/share it.
- Test changes to P1 tests: migration list assertions (test_db, test_accounts) include 007; `test_user_cannot_use_admin_operations` no longer asserts user POST /templates is 403; voice upload gate test updated (user now passes the gate, viewer account / operator key still 403).

## Tests
- New `tests/test_admin.py` (27 tests): users, tokens, admin identities, private templates, guardrails (hostile persona cannot remove/forge block; ordering; identical block across template kinds), user/admin voices, expert scoping, reingest. Contract tests extended (bodies for new ops, session and env-key RBAC loops).
- `uv run pytest -q -k "not live" tests -p no:cacheprovider` (includes tests/e2e/test_expectations.py): 558 passed, 5 skipped, 33 deselected (live), 0 failed, 187 s.

## Open loops / not done
- No UI (P3/P4). Reingest uses the service's cwd (as `POST /expert/experience` does for provenance); a deployment without git/manifests there gets 422 `provenance_unresolved` or a `missing_required` warning.
- `README.md` not updated (P4 owns docs); `docs/INTEGRATION.md` role table updated.
- No live/e2e runs (per instruction).
