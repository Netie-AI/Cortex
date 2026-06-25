-- node_executions: per-node LLM / route accounting (Cortex v2)
-- ceiling_myr stores the effective budget cap in force for that node execution (workflow vs node min).

CREATE TABLE IF NOT EXISTS node_executions (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_myr DOUBLE PRECISION NOT NULL,
    ceiling_myr DOUBLE PRECISION,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_node_executions_run_id ON node_executions (run_id);
CREATE INDEX IF NOT EXISTS idx_node_executions_started_at ON node_executions (started_at);
