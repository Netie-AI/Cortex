-- F6 captured skills (SQLite demo + Postgres target)
CREATE TABLE IF NOT EXISTS dms_skills (
    id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    embedding JSONB NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL,
    template JSONB NOT NULL DEFAULT '{}',
    support_count INTEGER NOT NULL DEFAULT 1,
    success_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TIMESTAMPTZ,
    created_by TEXT NOT NULL,
    consented BOOLEAN NOT NULL DEFAULT TRUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(intent, trigger_pattern)
);

CREATE INDEX IF NOT EXISTS idx_dms_skills_task ON dms_skills(task_id);
CREATE INDEX IF NOT EXISTS idx_dms_skills_active ON dms_skills(active, intent);

ALTER TABLE dms_skills ENABLE ROW LEVEL SECURITY;

CREATE POLICY dms_skills_tenant_select ON dms_skills
    FOR SELECT
    USING (current_setting('app.role', true) IN ('viewer', 'steward', 'admin'));

CREATE POLICY dms_skills_steward_write ON dms_skills
    FOR ALL
    USING (current_setting('app.role', true) IN ('steward', 'admin'))
    WITH CHECK (current_setting('app.role', true) IN ('steward', 'admin'));
