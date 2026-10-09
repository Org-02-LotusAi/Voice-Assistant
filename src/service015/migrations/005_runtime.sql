CREATE TABLE runtime_sessions (
    session_id  TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES identities(identity_id),
    room        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    end_reason  TEXT
);

-- t0..t5 are time.monotonic() seconds on the host; NULL until that point of the turn was observed.
CREATE TABLE turn_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL REFERENCES identities(identity_id),
    session_id  TEXT NOT NULL,
    turn_index  INTEGER NOT NULL,
    t0 REAL, t1 REAL, t2 REAL, t3 REAL, t4 REAL, t5 REAL,
    user_text   TEXT,
    agent_text  TEXT,
    interrupted INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_turn_metrics_identity ON turn_metrics (identity_id, session_id, turn_index);
