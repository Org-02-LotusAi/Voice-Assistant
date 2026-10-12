"""Phase 2 / P1 fix round 1: atomic throttle reservation, bounded argon2, rehash, voice probing, client IP."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from service015 import accounts
from service015.clientip import client_ip, parse_networks
from service015.db import connect, migrate
from tests.conftest import H, USER_PW, add_user, login_headers, make_app, make_settings
from tests.test_accounts import VOICE_AUTH, Sup, _own_voice, c, code, db, sup  # noqa: F401  (fixtures)

pytestmark = pytest.mark.usefixtures("fast_hash")


def test_concurrent_bad_logins_reserve_attempts_atomically(c, db_file, monkeypatch):
    """A burst of guesses cannot all pass the throttle check: at most the limit reach the password verify."""
    add_user(db_file, "uma")
    verifies, lock = [], threading.Lock()
    real = accounts.verify_password

    def counting(password_hash, password):
        with lock:
            verifies.append(1)
        time.sleep(0.05)
        return real(password_hash, password)

    monkeypatch.setattr(accounts, "verify_password", counting)

    def attempt(_):
        return c.post("/auth/login", json={"username": "uma", "password": "wrong-wrong-wrong"}).status_code

    with ThreadPoolExecutor(30) as pool:
        statuses = list(pool.map(attempt, range(30)))
    assert statuses.count(401) <= accounts.THROTTLE_USER_FAILURES
    assert statuses.count(401) + statuses.count(429) == 30, statuses
    assert len(verifies) == statuses.count(401) <= accounts.THROTTLE_USER_FAILURES


def test_concurrent_argon2_verifications_are_bounded(monkeypatch):
    state = {"now": 0, "max": 0}
    lock = threading.Lock()
    real = accounts._hasher

    class Slow:
        def verify(self, h, p):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return real.verify(h, p)

        def hash(self, p):
            return real.hash(p)

    h = real.hash("a-perfectly-fine-passphrase")
    monkeypatch.setattr(accounts, "_hasher", Slow())
    with ThreadPoolExecutor(16) as pool:
        results = list(pool.map(lambda _: accounts.verify_password(h, "a-perfectly-fine-passphrase"), range(16)))
    assert all(results) and state["max"] <= accounts.MAX_CONCURRENT_VERIFY


def test_change_password_reserves_before_verifying(c, db_file):
    add_user(db_file, "uma")
    h = login_headers(c, "uma")
    c.post("/auth/change-password", json={"current_password": "nope-nope-nope", "new_password": "A-brand-new-pass-77"}, headers=h)
    n = db(db_file).execute("SELECT COUNT(*) FROM login_attempts WHERE username = 'uma' AND success = 0").fetchone()[0]
    assert n == 1


def test_successful_login_releases_its_reservation(c, db_file):
    add_user(db_file, "uma")
    c.post("/auth/login", json={"username": "uma", "password": "wrong-wrong-wrong"})
    assert c.post("/auth/login", json={"username": "uma", "password": USER_PW}).status_code == 200
    assert db(db_file).execute("SELECT COUNT(*) FROM login_attempts WHERE username = 'uma'").fetchone()[0] == 0


def test_login_rehashes_an_outdated_hash(c, db_file, monkeypatch):
    add_user(db_file, "uma")
    before = db(db_file).execute("SELECT password_hash FROM users WHERE username='uma'").fetchone()[0]
    monkeypatch.setattr(accounts, "_hasher", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1))
    assert c.post("/auth/login", json={"username": "uma", "password": USER_PW}).status_code == 200
    after = db(db_file).execute("SELECT password_hash FROM users WHERE username='uma'").fetchone()[0]
    assert after != before and "t=2" in after and accounts.verify_password(after, USER_PW)


def test_dummy_hash_is_built_at_startup(db_file, voice_stack, monkeypatch):
    monkeypatch.setattr(accounts, "_dummy_hash", None)
    with TestClient(make_app(db_file, voice_stack)):
        assert accounts._dummy_hash is not None


def test_voice_existence_is_not_revealed_to_restricted_callers(c, db_file, voice_stack):
    uma_id, _ = add_user(db_file, "uma"), add_user(db_file, "wes")
    _own_voice(c, db_file, voice_stack, uma_id)
    voice_stack.voicebox.profiles["vp-bare"] = {"id": "vp-bare", "name": "Bare", "language": "en", "voice_type": "cloned"}
    wes = login_headers(c, "wes")
    answers = set()
    for pid in ("vp-uma", "vp-bare", "vp-nonexistent"):  # someone else's / unauthorized / missing
        r = c.post("/templates/sales-representative/instantiate", headers=wes,
                   json={"overrides": {"name": "B", "company": "C", "voice.profile_id": pid}})
        answers.add((r.status_code, code(r)))
        assert c.post(f"/voices/{pid}/preview", headers=wes).status_code == 404
    assert answers == {(422, "voice_profile_not_found")}
    r = c.post("/voices/vp-bare/preview", headers=H["operator"])  # unrestricted callers still learn it is unauthorized
    assert r.status_code == 422 and code(r) == "voice_not_authorized"


def test_bearer_takes_precedence_over_a_session_cookie(c, db_file):
    add_user(db_file, "uma")
    cookie = {"Cookie": login_headers(c, "uma")["Cookie"]}
    me = c.get("/auth/me", headers={**cookie, **H["viewer"]}).json()
    assert me["kind"] == "envkey" and me["role"] == "viewer"
    r = c.get("/auth/me", headers={**cookie, "Authorization": "Bearer not-a-key"})
    assert r.status_code == 401  # a bad bearer is final: no silent fallback to the cookie
    assert c.get("/auth/me", headers=cookie).json()["kind"] == "user"


def test_admin_lockout_is_per_ip_not_global():
    conn = connect(":memory:")
    migrate(conn)
    now = time.time()
    for _ in range(accounts.THROTTLE_USER_FAILURES):
        accounts.reserve_attempt(conn, "admin", "6.6.6.6", now=now)
    with pytest.raises(accounts.TooManyAttempts):
        accounts.reserve_attempt(conn, "admin", "6.6.6.6", now=now)
    accounts.reserve_attempt(conn, "admin", "10.0.0.5", now=now)  # the real admin, elsewhere, is not locked out
    for i in range(accounts.THROTTLE_USER_FAILURES):  # other usernames stay globally counted
        accounts.reserve_attempt(conn, "uma", f"9.9.9.{i}", now=now)
    with pytest.raises(accounts.TooManyAttempts):
        accounts.reserve_attempt(conn, "uma", "1.1.1.1", now=now)


def test_client_ip_trusts_forwarded_for_only_from_trusted_proxies():
    def req(peer, xff=None):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})

    trusted = parse_networks("172.18.0.0/16, 10.1.1.1")
    assert client_ip(req("172.18.0.5", "203.0.113.9"), trusted) == "203.0.113.9"
    assert client_ip(req("172.18.0.5", "198.51.100.1, 203.0.113.9"), trusted) == "203.0.113.9"  # forged left entry ignored
    assert client_ip(req("172.18.0.5", "203.0.113.9, 10.1.1.1"), trusted) == "203.0.113.9"  # trusted hops skipped
    assert client_ip(req("8.8.8.8", "203.0.113.9"), trusted) == "8.8.8.8"  # untrusted peer: header ignored
    assert client_ip(req("172.18.0.5"), trusted) == "172.18.0.5"
    assert client_ip(req("172.18.0.5", "garbage"), trusted) == "172.18.0.5"
    assert client_ip(req("172.18.0.5", "203.0.113.9"), ()) == "172.18.0.5"  # default: nothing trusted
    assert client_ip(req("testclient", "203.0.113.9"), trusted) == "testclient"
    with pytest.raises(ValueError):
        parse_networks("not-an-ip")


def test_trusted_proxies_setting_and_endpoint_ignores_untrusted_forwarded_for(db_file, voice_stack):
    s = make_settings(db_file, SERVICE015_TRUSTED_PROXIES="172.18.0.0/16,10.0.0.1")
    assert [str(n) for n in s.trusted_proxies] == ["172.18.0.0/16", "10.0.0.1/32"]
    assert make_settings(db_file).trusted_proxies == ()
    with TestClient(make_app(db_file, voice_stack)) as client:  # nothing trusted: the header must not dodge the per-IP limit
        for i in range(accounts.THROTTLE_IP_FAILURES):
            client.post("/auth/login", json={"username": f"ghost{i}", "password": "wrong-wrong-wrong"},
                        headers={"X-Forwarded-For": f"203.0.113.{i}"})
        r = client.post("/auth/login", json={"username": "ghost-x", "password": "x"}, headers={"X-Forwarded-For": "198.51.100.77"})
        assert r.status_code == 429
