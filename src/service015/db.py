"""SQLite connection and ordered SQL migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database (creating the parent directory) in WAL mode with FKs enforced."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")  # first: switching to WAL may need a lock
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: str | Path | None = None) -> list[str]:
    """Apply pending *.sql migrations in filename order, once each. Returns names applied now.

    Each file runs in its own BEGIN IMMEDIATE transaction together with its schema_migrations
    row, so concurrent starters serialize and a failing migration leaves nothing behind.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    source = Path(migrations_dir) if migrations_dir is not None else resources.files("service015").joinpath("migrations")
    files = sorted((f for f in source.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    done: list[str] = []
    for f in files:
        if _is_applied(conn, f.name):
            continue
        script = f.read_text(encoding="utf-8")
        stamp = datetime.now(timezone.utc).isoformat()
        record = "INSERT INTO schema_migrations (name, applied_at) VALUES ('{}', '{}');".format(
            f.name.replace("'", "''"), stamp
        )
        try:
            # executescript commits any pending transaction first, so the whole unit lives in the script.
            conn.executescript("\n".join(("BEGIN IMMEDIATE;", script, record, "COMMIT;")))
        except Exception:
            conn.rollback()
            if _is_applied(conn, f.name):
                continue  # another process applied it while we waited for the write lock
            raise
        done.append(f.name)
    return done


def _is_applied(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (name,)).fetchone() is not None
