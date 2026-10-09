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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations/*.sql in filename order, once each. Returns names applied now."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()
    applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(
        (f for f in resources.files("service015").joinpath("migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    done: list[str] = []
    for f in files:
        if f.name in applied:
            continue
        script = f.read_text(encoding="utf-8")
        stamp = datetime.now(timezone.utc).isoformat()
        try:
            # executescript would auto-commit first; BEGIN inside the script keeps DDL + record atomic.
            conn.executescript(f"BEGIN;\n{script}\n")
            conn.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)", (f.name, stamp))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        done.append(f.name)
    return done
