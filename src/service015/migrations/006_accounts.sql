-- Phase 2: accounts, sessions, DB-backed service tokens, login throttling, and ownership / archive / disable columns.

CREATE TABLE users (
    user_id              TEXT PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role                 TEXT NOT NULL CHECK (role IN ('admin','user','viewer')),
    password_hash        TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    disabled             INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    last_login_at        TEXT
);

-- session_hash is sha256 of the opaque cookie value: a leaked database does not yield usable sessions.
-- last_seen / expires are epoch seconds (idle timeout 12 h, absolute 7 d).
CREATE TABLE sessions (
    session_hash TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    csrf_token   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen    REAL NOT NULL,
    expires      REAL NOT NULL,
    ip           TEXT
);
CREATE INDEX idx_sessions_user ON sessions (user_id);

-- Machine bearer tokens managed by an admin; only the sha256 of the token is stored.
CREATE TABLE service_tokens (
    token_id     TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('viewer','operator')),
    token_hash   TEXT NOT NULL UNIQUE,
    created_by   TEXT REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE login_attempts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    ip       TEXT NOT NULL,
    success  INTEGER NOT NULL,
    at_epoch REAL NOT NULL
);
CREATE INDEX idx_login_attempts_user ON login_attempts (username, at_epoch);
CREATE INDEX idx_login_attempts_ip ON login_attempts (ip, at_epoch);

-- Ownership. NULL owner = unowned/system (machine-created); the bootstrap admin adopts pre-existing rows.
ALTER TABLE identities ADD COLUMN owner_user_id TEXT REFERENCES users(user_id);
ALTER TABLE identities ADD COLUMN archived_at TEXT;

ALTER TABLE templates ADD COLUMN owner_user_id TEXT REFERENCES users(user_id);
ALTER TABLE templates ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;
ALTER TABLE templates ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0;
UPDATE templates SET shared = 1;  -- every Phase-1 template was admin-managed and visible to all

ALTER TABLE voice_authorizations ADD COLUMN owner_user_id TEXT REFERENCES users(user_id);
ALTER TABLE voice_authorizations ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_identities_owner ON identities (owner_user_id);
