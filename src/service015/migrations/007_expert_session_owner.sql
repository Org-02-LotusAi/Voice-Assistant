-- Phase 2 / P2: Service Expert sessions are scoped to the principal that created them.
-- owner_key: 'user:<user_id>' for human sessions; NULL for machine (env key / service token) sessions.
ALTER TABLE expert_sessions ADD COLUMN owner_key TEXT;
