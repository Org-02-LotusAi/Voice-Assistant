import sqlite3

import pytest
from pydantic import ValidationError

from service015.config import Settings
from service015.db import connect, migrate

TABLES = {"templates", "template_versions", "identities", "runtime_status", "voice_authorizations"}


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return [r["name"] for r in rows]


def test_connect_sets_pragmas_and_creates_parent_dir(tmp_path):
    conn = connect(tmp_path / "nested" / "dir" / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.row_factory is sqlite3.Row


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert migrate(conn) == ["001_init.sql"]
    assert migrate(conn) == []
    names = _tables(conn)
    assert sorted(names) == sorted(TABLES | {"schema_migrations"})
    assert len(names) == len(set(names))
    rows = conn.execute("SELECT name, applied_at FROM schema_migrations").fetchall()
    assert [r["name"] for r in rows] == ["001_init.sql"]
    assert rows[0]["applied_at"]


def _seed_template_version(conn):
    conn.execute("INSERT INTO templates VALUES ('t', 'now')")
    conn.execute("INSERT INTO template_versions VALUES ('t', 1, '{}', 'now')")


def _insert_identity(conn, template_id, version):
    conn.execute(
        "INSERT INTO identities VALUES ('ava-001', ?, ?, '{}', '{}', 'h', 'ava-001', 'ava-001', 'now', 'now')",
        (template_id, version),
    )


def test_identity_foreign_key_to_template_versions(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    _seed_template_version(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_identity(conn, "t", 2)  # version 2 does not exist
    with pytest.raises(sqlite3.IntegrityError):
        _insert_identity(conn, "missing", 1)
    _insert_identity(conn, "t", 1)
    assert conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0] == 1


def test_presence_check_constraint(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    _seed_template_version(conn)
    _insert_identity(conn, "t", 1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO runtime_status (identity_id, presence) VALUES ('ava-001', 'BOGUS')")
    conn.execute("INSERT INTO runtime_status (identity_id, presence) VALUES ('ava-001', 'OFFLINE')")


def test_settings_parse_api_keys_into_roles():
    s = Settings(_env_file=None, SERVICE015_API_KEYS="k1:admin, k2:operator ,k3:viewer")
    assert s.api_keys == {"k1": "admin", "k2": "operator", "k3": "viewer"}


def test_settings_reads_api_keys_from_environment(monkeypatch):
    monkeypatch.setenv("SERVICE015_API_KEYS", "abc:viewer")
    monkeypatch.setenv("SERVICE015_PORT", "9000")
    s = Settings(_env_file=None)
    assert s.api_keys == {"abc": "viewer"}
    assert s.port == 9000


def test_settings_rejects_unknown_role():
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, SERVICE015_API_KEYS="k1:superuser")
    msg = str(exc.value)
    assert "unknown role 'superuser'" in msg
    assert "admin, operator, viewer" in msg


def test_settings_rejects_malformed_entry():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SERVICE015_API_KEYS="justakey")


def test_settings_defaults(monkeypatch):
    for var in ("SERVICE015_PORT", "LLM_MODEL", "LIVEKIT_URL", "VOICEBOX_URL", "SERVICE015_API_KEYS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.port == 8015
    assert s.llm_model == "llama-3.3-70b-versatile"
    assert s.livekit_url == "ws://localhost:7880"
    assert s.voicebox_url == "http://127.0.0.1:17493"
    assert s.api_keys == {}
