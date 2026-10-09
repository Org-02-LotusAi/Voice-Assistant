-- Scoped Service Expert: provenance-tracked knowledge, sessions, messages.
CREATE TABLE expert_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('document', 'verified_experience')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    sha TEXT NOT NULL,
    min_role TEXT NOT NULL DEFAULT 'viewer' CHECK (min_role IN ('viewer', 'operator', 'admin')),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_expert_knowledge_kind_source ON expert_knowledge (kind, source);

CREATE TABLE expert_sessions (
    session_id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
    created_at TEXT NOT NULL
);

CREATE TABLE expert_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES expert_sessions (session_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_expert_messages_session ON expert_messages (session_id, id);
