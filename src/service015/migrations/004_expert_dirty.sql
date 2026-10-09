-- Records whether the working tree was dirty when provenance was resolved (SHA may not match the text).
ALTER TABLE expert_knowledge ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0 CHECK (dirty IN (0, 1));
