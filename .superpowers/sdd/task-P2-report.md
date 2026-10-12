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

## Fix round 1
- I1: private template ids are namespaced per owner. A user's POST slug `sales` is stored and returned as `<username>.sales` (the dot is reserved; `TemplateCreate` keeps the dot-free slug pattern, stored ids use `STORED_TEMPLATE_ID_PATTERN`). `template_id_taken` removed: two users can both create `sales`; foreign ids are always 404. A user posting a shared slug just gets their own private family. Migration `008_private_template_namespace.sql` renames existing private families without bound identities (immutability triggers lifted and restored, `json_set` on the body); families with bound identities keep their old id (renaming would break identity FKs/hashes). Tested. Note: after an admin reassigns a private family to another user, that user can instantiate it but cannot version it (POST addresses their own namespace).
- I2: owner targets (identity, template, voice) must be active `user` accounts (422 `invalid_owner`; 404 unknown). Identity hand-over refuses with 409 `voice_not_accessible` when the voice is private to someone else, unless `clear_voice: true` (sets voice.profile_id null; launch then 422 `voice_required`). Launch preflight now checks voice visibility for the persona's owner (`authz.owner_visibility`; admin/unowned owners unrestricted).
- I3: spec change only: POST /templates and POST /voices are `x-required-role: admin` + `x-also-allowed: [user-session]` (new `require_author` dependency; `ensure_author` now also refuses viewer accounts). The hard-coded test exception is gone; the conformance tests read the extension, and a new test checks the extension against the routes.
- I4: reingest and `POST /expert/experience` use `SERVICE015_REPO_ROOT` or the repo containing the installed package (never cwd); a root without `.git` gives 422 `provenance_unresolved`. Tested with cwd elsewhere and a non-git root.
- Minors fixed: M1 (null fields in user patch rejected), M3 (actor in admin logs).
- Deferred: M2 (no per-user quota on templates/voice uploads), M4 (un-sharing an owner-less shared template), M5 (homoglyph markers), M6 (token expiry), M7 (document admin password recovery out-of-band), symlink following in `root.glob`.
- Also: `docs/SCHEMAS.md` regenerated (template_id model change).
- Tests: full offline suite `-k "not live"`: 568 passed, 5 skipped, 33 deselected, then SCHEMAS.md regenerated and its test re-run green. New tests in `tests/test_admin.py` (updated) and `tests/test_admin_fix1.py`.
