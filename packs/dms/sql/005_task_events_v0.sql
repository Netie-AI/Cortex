-- F5 task events (SQLite demo + Postgres target)
CREATE TABLE IF NOT EXISTS dms_task_events (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    thread_id TEXT,
    task_id TEXT NOT NULL,
    intent TEXT,
    filled_template JSONB NOT NULL DEFAULT '{}',
    gate_status TEXT CHECK (gate_status IN ('pass', 'warn', 'fail')),
    violations JSONB NOT NULL DEFAULT '[]',
    executable BOOLEAN NOT NULL DEFAULT FALSE,
    human_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dms_task_events_thread
    ON dms_task_events(thread_id, created_at);

ALTER TABLE dms_task_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY dms_task_events_tenant_select ON dms_task_events
    FOR SELECT
    USING (current_setting('app.role', true) IN ('viewer', 'steward', 'admin'));

CREATE POLICY dms_task_events_steward_write ON dms_task_events
    FOR ALL
    USING (current_setting('app.role', true) IN ('steward', 'admin'))
    WITH CHECK (current_setting('app.role', true) IN ('steward', 'admin'));
