import sqlite3

import pytest
from pydantic import ValidationError

from service015.config import Settings
from service015.db import connect, migrate

TABLES = {"templates", "template_versions", "identities", "runtime_status", "voice_authorizations",
          "expert_knowledge", "expert_sessions", "expert_messages", "runtime_sessions", "turn_metrics"}


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
    assert migrate(conn) == ["001_init.sql", "003_expert.sql", "004_expert_dirty.sql", "005_runtime.sql"]
    assert migrate(conn) == []
    names = _tables(conn)
    assert sorted(names) == sorted(TABLES | {"schema_migrations"})
    assert len(names) == len(set(names))
    rows = conn.execute("SELECT name, applied_at FROM schema_migrations").fetchall()
    assert [r["name"] for r in rows] == ["001_init.sql", "003_expert.sql", "004_expert_dirty.sql", "005_runtime.sql"]
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
    assert s.llm_model == "qwen/qwen3.8-27b"
    assert s.llm_reasoning_effort == "none"
    assert s.livekit_url == "ws://localhost:7880"
    assert s.voicebox_url == "http://127.0.0.1:17493"
    assert s.api_keys == {}


def test_settings_errors_do_not_echo_key_material():
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, SERVICE015_API_KEYS="SECRETKEY123:superuser")
    assert "SECRETKEY123" not in str(exc.value)
    assert "superuser" in str(exc.value)  # the offending role is named, not the key


def test_template_versions_are_immutable(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    _seed_template_version(conn)
    with pytest.raises(sqlite3.IntegrityError, match="template_versions rows are immutable"):
        conn.execute("UPDATE template_versions SET body_json = '{\"x\":1}' WHERE template_id = 't'")
    with pytest.raises(sqlite3.IntegrityError, match="template_versions rows are immutable"):
        conn.execute("DELETE FROM template_versions WHERE template_id = 't'")
    assert conn.execute("SELECT body_json FROM template_versions").fetchone()[0] == "{}"


def test_migrate_applies_files_in_filename_order(tmp_path):
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "002_b.sql").write_text("CREATE TABLE b (x INTEGER); INSERT INTO a VALUES (1);", encoding="utf-8")
    (mdir / "001_a.sql").write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")
    conn = connect(tmp_path / "t.db")
    assert migrate(conn, mdir) == ["001_a.sql", "002_b.sql"]  # 002 depends on 001's table
    assert migrate(conn, mdir) == []
    assert [r["name"] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY applied_at, name")] == [
        "001_a.sql",
        "002_b.sql",
    ]


def test_failing_migration_rolls_back_and_is_not_recorded(tmp_path):
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "001_ok.sql").write_text("CREATE TABLE ok (x INTEGER);", encoding="utf-8")
    (mdir / "002_bad.sql").write_text("CREATE TABLE half (x INTEGER); INSERT INTO nope VALUES (1);", encoding="utf-8")
    conn = connect(tmp_path / "t.db")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, mdir)
    assert "half" not in _tables(conn)
    assert "ok" in _tables(conn)
    assert [r["name"] for r in conn.execute("SELECT name FROM schema_migrations")] == ["001_ok.sql"]
    assert not conn.in_transaction


def test_livekit_public_url_is_what_browsers_get():
    s = Settings(SERVICE015_API_KEYS="a:admin", LIVEKIT_URL="ws://livekit:7880", _env_file=None)
    assert s.livekit_client_url == "ws://livekit:7880"  # unset -> same URL
    s = Settings(SERVICE015_API_KEYS="a:admin", LIVEKIT_URL="ws://livekit:7880",
                 LIVEKIT_PUBLIC_URL="wss://lk.example.com", _env_file=None)
    assert s.livekit_url == "ws://livekit:7880" and s.livekit_client_url == "wss://lk.example.com"


@pytest.mark.parametrize("keys", ["change-me-admin-key:admin", "short-key:operator"])
def test_weak_keys_refused_when_public_url_is_not_local(tmp_path, keys):
    from service015.api.app import create_app

    s = Settings(SERVICE015_API_KEYS=keys, SERVICE015_PUBLIC_URL="https://app.example.com",
                 SERVICE015_DB_PATH=str(tmp_path / "x.db"), _env_file=None)
    with pytest.raises(ValueError) as e:
        create_app(s)
    assert keys.split(":")[0] not in str(e.value)  # names the problem, never the key


def test_weak_keys_allowed_on_localhost_and_strong_keys_allowed_anywhere(tmp_path):
    from service015.config import check_api_keys_for_deployment

    check_api_keys_for_deployment(Settings(SERVICE015_API_KEYS="change-me:admin", SERVICE015_PUBLIC_URL="http://127.0.0.1:8015", _env_file=None))
    check_api_keys_for_deployment(Settings(SERVICE015_API_KEYS="change-me:admin", SERVICE015_PUBLIC_URL="http://localhost:8015", _env_file=None))
    check_api_keys_for_deployment(Settings(SERVICE015_API_KEYS="k" * 32 + ":admin", SERVICE015_PUBLIC_URL="https://app.example.com", _env_file=None))
