"""Phase 2 / P1: accounts, sessions, principals, ownership scoping, archive."""

import logging
import shutil
import time
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from service015 import accounts, identities_repo, templates_repo
from service015.db import connect, migrate
from service015.errors import ValidationFailed
from service015.models import TemplateCreate
from service015.voice import authz
from tests.conftest import (
    ADMIN_PW, H, USER_PW, FakeSupervisor, add_user, login_headers, make_app, make_settings,
)

pytestmark = pytest.mark.usefixtures("fast_hash")

VOICE_AUTH = {"consent_statement": "Synthetic voice.", "subject_name": "Zira", "authorized_by": "ops", "source": "test"}


class Sup(FakeSupervisor):
    def __init__(self, explode_stop=False):
        super().__init__()
        self.stopped = []
        self.explode_stop = explode_stop

    async def stop(self, identity_id):
        if self.explode_stop:
            from service015.errors import DependencyUnavailable

            raise DependencyUnavailable("dependency_unavailable", "cannot stop")
        self.stopped.append(identity_id)
        return self._status(identity_id)


@pytest.fixture
def sup():
    return Sup()


@pytest.fixture
def c(db_file, voice_stack, sup):
    with TestClient(make_app(db_file, voice_stack, sup, SERVICE015_ADMIN_PASSWORD=ADMIN_PW)) as client:
        assert client.post("/voices/vp-ava/authorizations", json=VOICE_AUTH, headers=H["admin"]).status_code == 201
        yield client


def db(db_file):
    return connect(db_file)


def persona(c, headers, name="Ava", **extra):
    r = c.post("/templates/sales-representative/instantiate", headers=headers,
               json={"overrides": {"name": name, "company": "Safi", "voice.profile_id": "vp-ava", **extra}})
    assert r.status_code == 201, r.text
    return r.json()["identity_id"]


def code(r):
    return r.json()["error"]["code"]


# ---------------------------------------------------------------- passwords and bootstrap

@pytest.mark.parametrize("pw,fragment", [("short", "at least 12"), ("x" * 129, "at most 128"), ("password1234", "common"),
                                         ("Admin", "at least 12")])
def test_password_policy_rejects(pw, fragment):
    with pytest.raises(ValidationFailed) as e:
        accounts.validate_password(pw, "someone")
    assert e.value.code == "weak_password" and fragment in e.value.message


def test_password_equal_to_username_rejected_and_good_one_accepted():
    with pytest.raises(ValidationFailed, match="username"):
        accounts.validate_password("same-as-the-name", "Same-As-The-Name")
    accounts.validate_password("a-perfectly-fine-passphrase", "someone")


@pytest.mark.parametrize("name", ["ab", "Upper", "-lead", "has space", "x" * 33, "bad!char"])
def test_username_rule(name):
    with pytest.raises(ValidationFailed) as e:
        accounts.validate_username(name)
    assert e.value.code == "invalid_username"


def test_real_hasher_produces_argon2id(monkeypatch):
    from argon2 import PasswordHasher

    monkeypatch.setattr(accounts, "_hasher", PasswordHasher())
    h = accounts.hash_password("a-perfectly-fine-passphrase")
    assert h.startswith("$argon2id$") and accounts.verify_password(h, "a-perfectly-fine-passphrase")
    assert not accounts.verify_password(h, "nope") and not accounts.verify_password(None, "nope")
    assert not accounts.verify_password("not-a-hash", "nope")


def test_bootstrap_creates_admin_requiring_password_change(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack, SERVICE015_ADMIN_PASSWORD=ADMIN_PW)):
        pass
    conn = db(db_file)
    row = accounts.find_user(conn, "ADMIN")  # NOCASE
    assert row["role"] == "admin" and row["must_change_password"] == 1 and row["disabled"] == 0
    assert ADMIN_PW not in row["password_hash"] and row["password_hash"].startswith("$argon2")
    # a second start does not create or change anything
    with TestClient(make_app(db_file, voice_stack, SERVICE015_ADMIN_PASSWORD="Another-Pass-1234")):
        pass
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert accounts.verify_password(accounts.find_user(conn, "admin")["password_hash"], ADMIN_PW)


def test_bootstrap_without_password_on_localhost_skips_with_warning(db_file, voice_stack, caplog):
    with caplog.at_level(logging.WARNING):
        with TestClient(make_app(db_file, voice_stack)) as client:
            assert client.get("/templates", headers=H["viewer"]).status_code == 200  # API keys still work
    assert db(db_file).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert "SERVICE015_ADMIN_PASSWORD" in caplog.text


def test_weak_bootstrap_password_refuses_to_start(db_file, voice_stack):
    with pytest.raises(ValueError, match="SERVICE015_ADMIN_PASSWORD"):
        make_app(db_file, voice_stack, SERVICE015_ADMIN_PASSWORD="short")


def test_unset_bootstrap_password_refuses_start_on_public_url(tmp_path):
    conn = connect(tmp_path / "p.db")
    migrate(conn)
    with pytest.raises(ValueError, match="SERVICE015_ADMIN_PASSWORD"):
        accounts.bootstrap_admin(conn, "", "https://app.example.com")
    assert accounts.bootstrap_admin(conn, "", "http://localhost:8015") is False
    assert accounts.bootstrap_admin(conn, ADMIN_PW, "https://app.example.com") is True
    assert accounts.bootstrap_admin(conn, "", "https://app.example.com") is False  # an admin exists: no password needed


def test_migration_assigns_existing_rows_to_the_bootstrap_admin(tmp_path):
    src = resources.files("service015").joinpath("migrations")
    old = tmp_path / "m_old"
    old.mkdir()
    for f in src.iterdir():
        if f.name.endswith(".sql") and f.name < "006":
            shutil.copy(str(f), old / f.name)
    conn = connect(tmp_path / "legacy.db")
    migrate(conn, old)
    conn.execute("INSERT INTO templates (template_id, created_at) VALUES ('legacy-t', 'now')")
    conn.execute("INSERT INTO template_versions VALUES ('legacy-t', 1, '{}', 'now')")
    conn.execute(
        "INSERT INTO identities (identity_id, template_id, template_version, overrides_json, resolved_json, resolved_hash, "
        "participant_identity, default_room, created_at, updated_at) VALUES ('old-001', 'legacy-t', 1, '{}', '{}', 'h', 'old-001', 'old-001', 'n', 'n')")
    conn.execute(
        "INSERT INTO voice_authorizations (voice_profile_id, authorized_by, subject_name, consent_statement, source, created_at) "
        "VALUES ('vp-old', 'a', 's', 'c', 'x', 'n')")
    conn.commit()
    assert migrate(conn)[0] == "006_accounts.sql"
    row = conn.execute("SELECT owner_user_id, shared, disabled FROM templates").fetchone()
    assert (row["owner_user_id"], row["shared"], row["disabled"]) == (None, 1, 0)  # pre-existing templates become shared
    assert accounts.bootstrap_admin(conn, ADMIN_PW, "http://localhost:8015")
    admin_id = accounts.find_user(conn, "admin")["user_id"]
    assert conn.execute("SELECT owner_user_id FROM identities").fetchone()[0] == admin_id
    assert conn.execute("SELECT owner_user_id FROM templates").fetchone()[0] == admin_id
    assert conn.execute("SELECT owner_user_id FROM voice_authorizations").fetchone()[0] == admin_id
    # the migrated voice is still shared (admin-owned) and the migrated identity is the admin's
    assert authz.voice_index(conn)["vp-old"]["shared"] is True
    assert conn.execute("SELECT identity_id FROM identities WHERE owner_user_id = ? AND archived_at IS NULL", (admin_id,)).fetchall()[0][0] == "old-001"


# ---------------------------------------------------------------- login / logout / me

def test_login_sets_hardened_cookie_and_returns_csrf(c, db_file):
    add_user(db_file, "uma")
    r = c.post("/auth/login", json={"username": "UMA", "password": USER_PW})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "uma" and body["user"]["role"] == "user" and body["csrf_token"]
    cookie = r.headers["set-cookie"].lower()
    assert "s015_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "secure" not in cookie.replace("samesite", "")  # plain-http local deployment
    raw = r.cookies.get("s015_session")
    stored = db(db_file).execute("SELECT session_hash FROM sessions").fetchone()[0]
    assert raw not in stored and len(stored) == 64  # only a hash is stored


def test_cookie_is_secure_over_https(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack), base_url="https://testserver") as client:
        add_user(db_file, "uma")
        r = client.post("/auth/login", json={"username": "uma", "password": USER_PW})
        assert "secure" in r.headers["set-cookie"].lower().replace("samesite", "")


def test_login_errors_are_generic(c, db_file):
    add_user(db_file, "uma")
    wrong_pw = c.post("/auth/login", json={"username": "uma", "password": "not-the-password"})
    no_user = c.post("/auth/login", json={"username": "ghost", "password": "not-the-password"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json() and code(wrong_pw) == "invalid_credentials"
    assert "s015_session" not in wrong_pw.headers.get("set-cookie", "")


def test_login_throttles_per_username_and_recovers_for_others(c, db_file):
    add_user(db_file, "uma")
    add_user(db_file, "wes")
    for _ in range(5):
        assert c.post("/auth/login", json={"username": "uma", "password": "wrong-wrong-wrong"}).status_code == 401
    r = c.post("/auth/login", json={"username": "uma", "password": USER_PW})  # right password, but locked
    assert r.status_code == 429 and code(r) == "too_many_attempts" and int(r.headers["retry-after"]) > 0
    assert c.post("/auth/login", json={"username": "wes", "password": USER_PW}).status_code == 200
    # unknown usernames are throttled identically (no enumeration through the 429)
    for _ in range(5):
        c.post("/auth/login", json={"username": "ghost", "password": "wrong-wrong-wrong"})
    assert c.post("/auth/login", json={"username": "ghost", "password": "x"}).status_code == 429


def test_login_throttles_per_ip(c, db_file):
    add_user(db_file, "uma")
    for i in range(accounts.THROTTLE_IP_FAILURES):
        assert c.post("/auth/login", json={"username": f"ghost{i}", "password": "wrong-wrong-wrong"}).status_code == 401
    r = c.post("/auth/login", json={"username": "uma", "password": USER_PW})
    assert r.status_code == 429 and code(r) == "too_many_attempts"


def test_throttle_window_expires(db_file):
    conn = connect(db_file)
    migrate(conn)
    now = time.time()
    for _ in range(5):
        accounts.record_attempt(conn, "uma", "1.2.3.4", False, now=now - accounts.THROTTLE_WINDOW_S - 5)
    accounts.check_throttle(conn, "uma", "1.2.3.4", now=now)  # old failures no longer count
    for _ in range(5):
        accounts.record_attempt(conn, "uma", "1.2.3.4", False, now=now - 10)
    with pytest.raises(accounts.TooManyAttempts):
        accounts.check_throttle(conn, "uma", "1.2.3.4", now=now)


def test_secrets_are_never_logged(c, db_file, caplog):
    add_user(db_file, "uma")
    with caplog.at_level(logging.DEBUG):
        c.post("/auth/login", json={"username": "uma", "password": "wrong-wrong-wrong"})
        r = c.post("/auth/login", json={"username": "uma", "password": USER_PW})
        raw = r.cookies.get("s015_session")
        h = {"Cookie": f"s015_session={raw}", "X-CSRF-Token": r.json()["csrf_token"]}
        c.post("/auth/change-password", json={"current_password": USER_PW, "new_password": "Brand-New-Pass-4242"}, headers=h)
    for secret in (USER_PW, "wrong-wrong-wrong", "Brand-New-Pass-4242", raw, r.json()["csrf_token"]):
        assert secret not in caplog.text


def test_me_for_each_principal_kind(c, db_file):
    uid = add_user(db_file, "uma")
    me = c.get("/auth/me", headers=login_headers(c, "uma")).json()
    assert (me["kind"], me["role"], me["effective_role"], me["user_id"], me["username"]) == ("user", "user", "operator", uid, "uma")
    assert me["csrf_token"]
    me = c.get("/auth/me", headers=H["operator"]).json()
    assert (me["kind"], me["role"], me["user_id"], me["csrf_token"]) == ("envkey", "operator", None, None)
    assert c.get("/auth/me").status_code == 401


def test_logout_revokes_session_and_clears_cookie(c, db_file):
    add_user(db_file, "uma")
    h = login_headers(c, "uma")
    r = c.post("/auth/logout", headers=h)
    assert r.status_code == 204 and "s015_session" in r.headers["set-cookie"]
    assert c.get("/identities", headers=h).status_code == 401
    assert db(db_file).execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert c.post("/auth/logout", headers=H["viewer"]).status_code == 204  # bearer: harmless no-op


def test_idle_and_absolute_session_expiry_and_sliding(c, db_file):
    add_user(db_file, "uma")
    h = login_headers(c, "uma")
    conn = db(db_file)
    conn.execute("UPDATE sessions SET last_seen = last_seen - 600")
    conn.commit()
    assert c.get("/identities", headers=h).status_code == 200
    assert conn.execute("SELECT last_seen FROM sessions").fetchone()[0] > time.time() - 30  # slid forward
    conn.execute("UPDATE sessions SET last_seen = last_seen - ?", (accounts.IDLE_TIMEOUT_S + 60,))
    conn.commit()
    r = c.get("/identities", headers=h)
    assert r.status_code == 401 and code(r) == "unauthorized"
    h = login_headers(c, "uma")
    conn.execute("UPDATE sessions SET expires = ?", (time.time() - 1,))
    conn.commit()
    assert c.get("/identities", headers=h).status_code == 401
    assert accounts.ABSOLUTE_TIMEOUT_S == 7 * 86400 and accounts.IDLE_TIMEOUT_S == 12 * 3600


def test_disabled_user_cannot_login_or_use_a_live_session(c, db_file):
    uid = add_user(db_file, "uma")
    h = login_headers(c, "uma")
    conn = db(db_file)
    conn.execute("UPDATE users SET disabled = 1 WHERE user_id = ?", (uid,))
    conn.commit()
    assert c.get("/identities", headers=h).status_code == 401
    r = c.post("/auth/login", json={"username": "uma", "password": USER_PW})
    assert r.status_code == 401 and code(r) == "invalid_credentials"


# ---------------------------------------------------------------- CSRF

def test_csrf_required_for_cookie_writes_not_reads_not_bearer(c, db_file):
    add_user(db_file, "uma")
    h = login_headers(c, "uma")
    aid = persona(c, h)
    cookie_only = {"Cookie": h["Cookie"]}
    assert c.get(f"/identities/{aid}", headers=cookie_only).status_code == 200
    for bad in (cookie_only, {**cookie_only, "X-CSRF-Token": "wrong"}):
        r = c.post(f"/identities/{aid}/stop", headers=bad)
        assert r.status_code == 403 and code(r) == "csrf_failed"
    assert c.post(f"/identities/{aid}/stop", headers=h).status_code == 200
    # a Bearer header wins over a cookie and needs no CSRF token
    assert c.post(f"/identities/{aid}/stop", headers={**cookie_only, **H["operator"]}).status_code == 200


# ---------------------------------------------------------------- must-change-password gate

def test_forced_password_change_flow(c, db_file):
    r = c.post("/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    old = {"Cookie": f"s015_session={r.cookies.get('s015_session')}", "X-CSRF-Token": r.json()["csrf_token"]}
    c.cookies.clear()
    other = login_headers(c, "admin", ADMIN_PW)  # a second session of the same account
    for path in ("/identities", "/templates", "/voices", "/meeting/participants"):
        r = c.get(path, headers=old)
        assert r.status_code == 403 and code(r) == "password_change_required", path
    r = c.post("/templates/sales-representative/instantiate", headers=old, json={"overrides": {}})
    assert r.status_code == 403 and code(r) == "password_change_required"
    assert c.get("/auth/me", headers=old).json()["must_change_password"] is True
    # weak / wrong / unchanged passwords are refused and the gate stays
    for body, expected in (
        ({"current_password": ADMIN_PW, "new_password": "short"}, "weak_password"),
        ({"current_password": ADMIN_PW, "new_password": "password1234"}, "weak_password"),
        ({"current_password": ADMIN_PW, "new_password": "admin"}, "weak_password"),
        ({"current_password": "not-my-password", "new_password": "Fresh-Admin-Pass-31"}, "invalid_credentials"),
        ({"current_password": ADMIN_PW, "new_password": ADMIN_PW}, "password_unchanged"),
    ):
        r = c.post("/auth/change-password", json=body, headers=old)
        assert r.status_code == 422 and code(r) == expected, (body, r.text)
    r = c.post("/auth/change-password", json={"current_password": ADMIN_PW, "new_password": "Fresh-Admin-Pass-31"}, headers=old)
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert c.get("/identities", headers=old).status_code == 200  # same session, gate lifted
    assert c.get("/identities", headers=other).status_code == 401  # other sessions revoked
    assert c.post("/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 401
    assert c.post("/auth/login", json={"username": "admin", "password": "Fresh-Admin-Pass-31"}).status_code == 200


def test_change_password_needs_a_session_and_throttles_wrong_current(c, db_file):
    r = c.post("/auth/change-password", json={"current_password": "x", "new_password": "A-brand-new-pass-77"}, headers=H["admin"])
    assert r.status_code == 403 and code(r) == "session_required"
    add_user(db_file, "uma")
    h = login_headers(c, "uma")
    for _ in range(5):
        c.post("/auth/change-password", json={"current_password": "nope-nope-nope", "new_password": "A-brand-new-pass-77"}, headers=h)
    r = c.post("/auth/change-password", json={"current_password": USER_PW, "new_password": "A-brand-new-pass-77"}, headers=h)
    assert r.status_code == 429


# ---------------------------------------------------------------- service tokens and env keys

def test_db_service_token_authenticates_with_its_role_and_can_be_revoked(c, db_file):
    conn = db(db_file)
    row, token = accounts.create_service_token(conn, "meeting-manager", "viewer")
    stored = conn.execute("SELECT token_hash FROM service_tokens").fetchone()[0]
    assert token.startswith("s015_") and token not in stored and stored == accounts.sha256_hex(token)
    auth = {"Authorization": f"Bearer {token}"}
    me = c.get("/auth/me", headers=auth).json()
    assert (me["kind"], me["role"], me["label"]) == ("token", "viewer", "meeting-manager")
    assert c.get("/meeting/participants", headers=auth).status_code == 200
    r = c.post("/identities/x/stop", headers=auth)
    assert r.status_code == 403 and r.json()["error"]["details"] == {"required_role": "operator", "role": "viewer"}
    accounts.revoke_service_token(conn, row["token_id"])
    assert c.get("/meeting/participants", headers=auth).status_code == 401
    with pytest.raises(Exception):
        accounts.revoke_service_token(conn, row["token_id"])  # already revoked


def test_operator_token_is_global_like_an_env_key(c, db_file):
    add_user(db_file, "uma")
    aid = persona(c, login_headers(c, "uma"))
    _, token = accounts.create_service_token(db(db_file), "ci", "operator")
    auth = {"Authorization": f"Bearer {token}"}
    assert c.get(f"/identities/{aid}", headers=auth).status_code == 200
    assert c.post(f"/identities/{aid}/stop", headers=auth).status_code == 200
    mine = persona(c, auth, name="Tokenbot")  # machine-created: no owner
    assert [i["owner_user_id"] for i in c.get("/identities", headers=auth).json() if i["identity_id"] == mine] == [None]


# ---------------------------------------------------------------- owner scoping: personas

def test_user_sees_and_touches_only_own_personas(c, db_file):
    uma_id, wes_id = add_user(db_file, "uma"), add_user(db_file, "wes")
    uma, wes = login_headers(c, "uma"), login_headers(c, "wes")
    a = persona(c, uma, "Ava")
    b = persona(c, wes, "Ben")
    m = persona(c, H["operator"], "Machine")
    assert [i["identity_id"] for i in c.get("/identities", headers=uma).json()] == [a]
    assert [i["identity_id"] for i in c.get("/identities", headers=wes).json()] == [b]
    assert {i["identity_id"] for i in c.get("/identities", headers=H["operator"]).json()} == {a, b, m}
    assert {i["identity_id"] for i in c.get("/identities", headers=H["viewer"]).json()} == {a, b, m}
    owners = {i["identity_id"]: i["owner_user_id"] for i in c.get("/identities", headers=H["admin"]).json()}
    assert owners == {a: uma_id, b: wes_id, m: None}
    calls = [
        ("get", f"/identities/{b}", None), ("patch", f"/identities/{b}", {"overrides": {"role": "Lead"}}),
        ("get", f"/identities/{b}/self-context", None), ("post", f"/identities/{b}/launch", None),
        ("post", f"/identities/{b}/stop", None), ("get", f"/identities/{b}/status", None),
        ("post", f"/identities/{b}/join-token", None), ("get", f"/identities/{b}/latency", None),
        ("post", f"/identities/{b}/phone/dial", {"to": "+919876543210"}), ("get", f"/identities/{b}/phone", None),
        ("delete", f"/identities/{b}", None), ("get", "/identities/never-001", None),
    ]
    for method, path, body in calls:
        r = c.request(method, path, headers=uma, **({"json": body} if body else {}))
        assert r.status_code == 404 and code(r) == "identity_not_found", (method, path, r.text)
    for method, path, body in calls[:-1]:  # the owner (and the global operator) get through the same routes
        r = c.request(method, path, headers=wes if method != "delete" else H["operator"], **({"json": body} if body else {}))
        assert r.status_code != 404, (method, path)
    # roster is scoped too
    assert [p["identity_id"] for p in c.get("/meeting/participants", headers=uma).json()] == [a]
    assert {p["identity_id"] for p in c.get("/meeting/participants", headers=H["viewer"]).json()} == {a, m}  # b archived above


def test_viewer_account_reads_everything_but_writes_nothing(c, db_file):
    add_user(db_file, "uma")
    add_user(db_file, "vic", "viewer")
    aid = persona(c, login_headers(c, "uma"))
    vic = login_headers(c, "vic")
    assert c.get(f"/identities/{aid}", headers=vic).status_code == 200
    assert c.get(f"/identities/{aid}/status", headers=vic).status_code == 200
    assert c.get(f"/identities/{aid}/latency", headers=vic).status_code == 200
    assert [p["identity_id"] for p in c.get("/meeting/participants", headers=vic).json()] == [aid]
    for method, path in (("post", f"/identities/{aid}/launch"), ("post", f"/identities/{aid}/stop"),
                         ("delete", f"/identities/{aid}"), ("post", "/templates/sales-representative/instantiate")):
        r = c.request(method, path, headers=vic, json={"overrides": {}} if "instantiate" in path else None)
        assert r.status_code == 403 and code(r) == "forbidden", path


def test_user_cannot_use_admin_operations(c, db_file):
    add_user(db_file, "uma")
    uma = login_headers(c, "uma")
    assert c.post("/voices/vp-ava/authorizations", headers=uma, json=VOICE_AUTH).status_code == 403
    assert c.post("/expert/experience", headers=uma, json={"title": "t", "body": "b", "source": "s"}).status_code == 403


def test_voice_upload_gate_runs_before_the_body_is_read(c, db_file):
    add_user(db_file, "uma")
    add_user(db_file, "vic", "viewer")
    uma = login_headers(c, "uma")
    vic = login_headers(c, "vic")
    files = {"file": ("a.wav", b"x" * 1000, "audio/wav")}
    assert c.post("/voices", files=files, data={"name": "n"}, headers=vic).status_code == 403  # viewer accounts never upload
    assert c.post("/voices", files=files, data={"name": "n"}, headers=H["operator"]).status_code == 403  # nor operator keys
    assert c.post("/voices", files=files, data={"name": "n"}, headers=uma).status_code == 422  # a user passes the gate: form incomplete
    assert c.post("/voices", files=files, data={"name": "n"}, headers={"Cookie": uma["Cookie"]}).status_code == 403  # csrf_failed
    assert c.post("/voices", files=files, data={"name": "n"}).status_code == 401


# ---------------------------------------------------------------- archive

def test_archive_stops_a_running_persona_hides_it_and_never_reuses_the_id(c, db_file, sup):
    add_user(db_file, "uma")
    uma = login_headers(c, "uma")
    aid = persona(c, uma)
    conn = db(db_file)
    identities_repo.set_status(conn, aid, "READY")
    r = c.delete(f"/identities/{aid}", headers=uma)
    assert r.status_code == 200 and r.json()["identity_id"] == aid and r.json()["archived_at"]
    assert sup.stopped == [aid]
    assert c.get("/identities", headers=uma).json() == [] and c.get("/identities", headers=H["admin"]).json() == []
    assert c.get("/meeting/participants", headers=H["admin"]).json() == []
    for path in (f"/identities/{aid}", f"/identities/{aid}/status", f"/identities/{aid}/self-context"):
        assert c.get(path, headers=H["admin"]).status_code == 404
    assert c.delete(f"/identities/{aid}", headers=uma).status_code == 404  # already archived
    row = conn.execute("SELECT archived_at FROM identities WHERE identity_id = ?", (aid,)).fetchone()
    assert row["archived_at"]  # retained, not deleted
    assert conn.execute("SELECT 1 FROM runtime_status WHERE identity_id = ?", (aid,)).fetchone()
    assert persona(c, uma) != aid  # ava-002, the id is never reused
    assert [i.identity_id for i in identities_repo.list_identities(conn, include_archived=True)] == [aid, persona_ids(conn)[1]]


def persona_ids(conn):
    return [r["identity_id"] for r in conn.execute("SELECT identity_id FROM identities ORDER BY identity_id")]


def test_archive_of_an_offline_persona_does_not_call_the_runtime(c, db_file, sup):
    add_user(db_file, "uma")
    uma = login_headers(c, "uma")
    aid = persona(c, uma)
    assert c.delete(f"/identities/{aid}", headers=uma).status_code == 200
    assert sup.stopped == []


def test_archive_fails_loudly_when_the_runtime_cannot_be_stopped(db_file, voice_stack):
    sup = Sup(explode_stop=True)
    with TestClient(make_app(db_file, voice_stack, sup, SERVICE015_ADMIN_PASSWORD=ADMIN_PW)) as c:
        c.post("/voices/vp-ava/authorizations", json=VOICE_AUTH, headers=H["admin"])
        aid = persona(c, H["operator"])
        identities_repo.set_status(connect(db_file), aid, "READY")
        r = c.delete(f"/identities/{aid}", headers=H["operator"])
        assert r.status_code == 503
        assert [i["identity_id"] for i in c.get("/identities", headers=H["operator"]).json()] == [aid]  # still there


def test_archive_with_unconfigured_runtime_works_when_offline(db_file, voice_stack):
    with TestClient(make_app(db_file, voice_stack)) as c:  # NullSupervisor
        c.post("/voices/vp-ava/authorizations", json=VOICE_AUTH, headers=H["admin"])
        aid = persona(c, H["operator"])
        assert c.delete(f"/identities/{aid}", headers=H["operator"]).status_code == 200


# ---------------------------------------------------------------- templates

def _private_template(db_file, owner_id, template_id="uma-private"):
    conn = db(db_file)
    body = templates_repo.get_template(conn, "customer-support")
    data = {k: v for k, v in body.model_dump().items() if k not in ("version", "created_at")}
    data["template_id"] = template_id
    templates_repo.create_template(conn, TemplateCreate.model_validate(data), owner_user_id=owner_id, shared=False)


def test_template_visibility_shared_private_and_admin(c, db_file):
    uma_id, _ = add_user(db_file, "uma"), add_user(db_file, "wes")
    _private_template(db_file, uma_id)
    uma, wes = login_headers(c, "uma"), login_headers(c, "wes")

    def ids(h):
        return {t["template_id"] for t in c.get("/templates", headers=h).json()}

    shared = {"customer-support", "sales-representative"}
    assert shared <= ids(wes) and "uma-private" not in ids(wes)
    assert "uma-private" in ids(uma) and shared <= ids(uma)
    assert "uma-private" in ids(H["admin"]) and "uma-private" in ids(H["operator"]) and "uma-private" in ids(H["viewer"])
    for path in ("/templates/uma-private", "/templates/uma-private/versions"):
        assert c.get(path, headers=wes).status_code == 404
        assert c.get(path, headers=uma).status_code == 200
    r = c.post("/templates/uma-private/instantiate", headers=wes, json={"overrides": {"name": "X", "company": "Y"}})
    assert r.status_code == 404 and code(r) == "template_not_found"
    r = c.post("/templates/uma-private/instantiate", headers=uma, json={"overrides": {"name": "Ava", "company": "Y"}})
    assert r.status_code == 201
    summary = {t["template_id"]: t for t in c.get("/templates", headers=H["admin"]).json()}
    assert summary["uma-private"]["shared"] is False and summary["uma-private"]["owner_user_id"] == uma_id
    assert summary["customer-support"]["shared"] is True and summary["customer-support"]["disabled"] is False


def test_viewer_account_sees_only_shared_templates(c, db_file):
    uma_id = add_user(db_file, "uma")
    add_user(db_file, "vic", "viewer")
    _private_template(db_file, uma_id)
    ids = {t["template_id"] for t in c.get("/templates", headers=login_headers(c, "vic")).json()}
    assert "uma-private" not in ids and "customer-support" in ids


def test_disabled_template_blocks_new_instances_but_not_existing_ones(c, db_file):
    add_user(db_file, "uma")
    uma = login_headers(c, "uma")
    aid = persona(c, uma)
    conn = db(db_file)
    conn.execute("UPDATE templates SET disabled = 1 WHERE template_id = 'sales-representative'")
    conn.commit()
    for h in (uma, H["operator"], H["admin"]):
        r = c.post("/templates/sales-representative/instantiate", headers=h, json={"overrides": {"name": "B", "company": "C"}})
        assert r.status_code == 409 and code(r) == "template_disabled"
    assert c.get(f"/identities/{aid}", headers=uma).status_code == 200  # existing persona unaffected
    assert c.get("/templates/sales-representative", headers=uma).status_code == 200
    assert {t["template_id"]: t["disabled"] for t in c.get("/templates", headers=uma).json()}["sales-representative"] is True


def test_created_templates_are_shared_and_owned_by_the_admin_account(c, db_file):
    admin_id = add_user(db_file, "ada", "admin")
    ada = login_headers(c, "ada")
    body = c.get("/templates/customer-support", headers=ada).json()
    body = {k: v for k, v in body.items() if k not in ("version", "created_at")}
    body["template_id"] = "ada-shared"
    assert c.post("/templates", headers=ada, json=body).status_code == 201
    summary = {t["template_id"]: t for t in c.get("/templates", headers=login_headers(c, "ada")).json()}["ada-shared"]
    assert summary["shared"] is True and summary["owner_user_id"] == admin_id


# ---------------------------------------------------------------- voices

def _own_voice(c, db_file, voice_stack, owner_id, pid="vp-uma"):
    voice_stack.voicebox.profiles[pid] = {"id": pid, "name": "Uma Voice", "language": "en", "voice_type": "cloned"}
    conn = db(db_file)
    authz.record_authorization(conn, pid, "uma", "Uma", "I consent.", "test", owner_id)


def test_voice_listing_and_attachment_are_scoped(c, db_file, voice_stack):
    uma_id, _ = add_user(db_file, "uma"), add_user(db_file, "wes")
    _own_voice(c, db_file, voice_stack, uma_id)
    uma, wes = login_headers(c, "uma"), login_headers(c, "wes")

    def ids(h):
        return {v["voice_profile_id"] for v in c.get("/voices", headers=h).json()}

    assert ids(uma) == {"vp-ava", "vp-uma"} and ids(wes) == {"vp-ava"}
    assert ids(H["viewer"]) == ids(H["admin"]) == ids(H["operator"]) == {"vp-ava", "vp-uma"}
    ov = {"name": "Ben", "company": "Safi", "voice.profile_id": "vp-uma"}
    r = c.post("/templates/sales-representative/instantiate", headers=wes, json={"overrides": ov})
    assert r.status_code == 422 and code(r) == "voice_profile_not_found"
    assert c.post("/templates/sales-representative/instantiate", headers=uma, json={"overrides": ov}).status_code == 201
    aid = persona(c, wes)  # wes patches in someone else's voice -> refused; a global operator may
    r = c.patch(f"/identities/{aid}", headers=wes, json={"overrides": {"voice.profile_id": "vp-uma"}})
    assert r.status_code == 422 and code(r) == "voice_profile_not_found"
    assert c.patch(f"/identities/{aid}", headers=H["operator"], json={"overrides": {"voice.profile_id": "vp-uma"}}).status_code == 200
    assert c.post("/voices/vp-uma/preview", headers=wes).status_code == 404
    assert c.post("/voices/vp-uma/preview", headers=uma).status_code == 200


def test_admin_owned_voice_is_shared(c, db_file, voice_stack):
    admin_id = add_user(db_file, "ada", "admin")
    _own_voice(c, db_file, voice_stack, admin_id, pid="vp-adm")
    add_user(db_file, "wes")
    assert "vp-adm" in {v["voice_profile_id"] for v in c.get("/voices", headers=login_headers(c, "wes")).json()}


def test_disabled_voice_blocks_attachment_preview_and_launch(c, db_file, voice_stack):
    add_user(db_file, "uma")
    uma = login_headers(c, "uma")
    aid = persona(c, uma)
    authz.set_voice_disabled(db(db_file), "vp-ava", True)
    r = c.post("/templates/sales-representative/instantiate", headers=uma,
               json={"overrides": {"name": "B", "company": "C", "voice.profile_id": "vp-ava"}})
    assert r.status_code == 422 and code(r) == "voice_disabled"
    assert c.patch(f"/identities/{aid}", headers=uma, json={"overrides": {"voice.profile_id": None}}).status_code == 200
    r = c.patch(f"/identities/{aid}", headers=uma, json={"overrides": {"voice.profile_id": "vp-ava"}})
    assert r.status_code == 422 and code(r) == "voice_disabled"
    r = c.post("/voices/vp-ava/preview", headers=H["operator"])
    assert r.status_code == 422 and code(r) == "voice_disabled"
    # an already-attached disabled voice cannot launch
    authz.set_voice_disabled(db(db_file), "vp-ava", False)
    pid = persona(c, uma, "Third")
    authz.set_voice_disabled(db(db_file), "vp-ava", True)
    r = c.post(f"/identities/{pid}/launch", headers=uma)
    assert r.status_code == 422 and code(r) == "voice_disabled"
    authz.set_voice_disabled(db(db_file), "vp-ava", False)
    assert c.post(f"/identities/{pid}/launch", headers=uma).status_code == 200
