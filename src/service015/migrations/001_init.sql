CREATE TABLE templates (
    template_id TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE template_versions (
    template_id TEXT NOT NULL REFERENCES templates(template_id),
    version     INTEGER NOT NULL,
    body_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (template_id, version)
);

CREATE TABLE identities (
    identity_id          TEXT PRIMARY KEY,
    template_id          TEXT NOT NULL,
    template_version     INTEGER NOT NULL,
    overrides_json       TEXT NOT NULL,
    resolved_json        TEXT NOT NULL,
    resolved_hash        TEXT NOT NULL,
    participant_identity TEXT NOT NULL,
    default_room         TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (template_id, template_version) REFERENCES template_versions(template_id, version)
);

CREATE TABLE runtime_status (
    identity_id   TEXT PRIMARY KEY REFERENCES identities(identity_id),
    presence      TEXT NOT NULL CHECK (presence IN ('READY','STARTING','BUSY','ERROR','OFFLINE')),
    status_detail TEXT,
    pid           INTEGER,
    launched_at   TEXT,
    heartbeat_at  TEXT,
    last_error    TEXT
);

CREATE TABLE voice_authorizations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    voice_profile_id  TEXT NOT NULL,
    authorized_by     TEXT NOT NULL,
    subject_name      TEXT NOT NULL,
    consent_statement TEXT NOT NULL,
    source            TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
