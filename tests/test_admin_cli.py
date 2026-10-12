"""`service015-admin reset-password`: out-of-band recovery."""

import pytest
from fastapi.testclient import TestClient

from service015 import accounts
from service015.admin_cli import ENV_VAR, main
from service015.db import connect
from tests.conftest import ADMIN_PW, USER_PW, FakeSupervisor, add_user, make_app

pytestmark = pytest.mark.usefixtures("fast_hash")
NEW_PW = "Recovered-Pass-2026-x"


def test_reset_password_sets_must_change_enables_and_revokes_sessions(db_file, voice_stack, monkeypatch, capsys):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), SERVICE015_ADMIN_PASSWORD=ADMIN_PW)) as c:
        add_user(db_file, "ada")
        assert c.post("/auth/login", json={"username": "ada", "password": USER_PW}).status_code == 200
        conn = connect(db_file)
        conn.execute("UPDATE users SET disabled = 1 WHERE username = 'ada'"); conn.commit(); conn.close()
        monkeypatch.setenv("SERVICE015_DB_PATH", str(db_file))
        monkeypatch.setenv(ENV_VAR, NEW_PW)
        assert main(["reset-password", "Ada"]) == 0
        out = capsys.readouterr()
        assert NEW_PW not in out.out + out.err
        conn = connect(db_file)
        try:
            row = accounts.find_user(conn, "ada")
            assert row["must_change_password"] == 1 and row["disabled"] == 0
            assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (row["user_id"],)).fetchone()[0] == 0
        finally:
            conn.close()
        assert c.get("/auth/me").status_code == 401  # the old session is gone
        r = c.post("/auth/login", json={"username": "ada", "password": NEW_PW})
        assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
        assert c.post("/auth/login", json={"username": "ada", "password": USER_PW}).status_code == 401


def test_reset_password_rejects_unknown_user_and_weak_password(db_file, voice_stack, monkeypatch, capsys):
    with TestClient(make_app(db_file, voice_stack, FakeSupervisor(), SERVICE015_ADMIN_PASSWORD=ADMIN_PW)):
        monkeypatch.setenv("SERVICE015_DB_PATH", str(db_file))
        monkeypatch.setenv(ENV_VAR, NEW_PW)
        assert main(["reset-password", "nobody-here"]) == 1
        monkeypatch.setenv(ENV_VAR, "short")
        assert main(["reset-password", "admin"]) == 1
