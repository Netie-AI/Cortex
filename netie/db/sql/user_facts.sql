-- Semantic memory: durable user facts keyed for prompt injection.

CREATE TABLE IF NOT EXISTS user_facts (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

CREATE INDEX IF NOT EXISTS idx_user_facts_updated ON user_facts (user_id, updated_at DESC);
