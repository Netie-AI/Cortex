-- F6 captured skills (Postgres target)
CREATE TABLE IF NOT EXISTS dms_skills (
    id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    trigger_pattern TEXT NOT NULL,
    embedding JSONB NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL,
    template JSONB NOT NULL DEFAULT '{}',
    support_count INTEGER NOT NULL DEFAULT 1,
    success_count INTEGER NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    created_by TEXT NOT NULL,
    consented BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (intent, trigger_pattern)
);

CREATE INDEX IF NOT EXISTS idx_dms_skills_task ON dms_skills(task_id) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_dms_skills_intent ON dms_skills(intent) WHERE active = TRUE;

ALTER TABLE dms_skills ENABLE ROW LEVEL SECURITY;

CREATE POLICY dms_skills_tenant_select ON dms_skills
    FOR SELECT
    USING (current_setting('app.role', true) IN ('viewer', 'steward', 'admin'));

CREATE POLICY dms_skills_steward_write ON dms_skills
    FOR ALL
    USING (current_setting('app.role', true) IN ('steward', 'admin'))
    WITH CHECK (current_setting('app.role', true) IN ('steward', 'admin'));

-- Outcome tracking on task events (F6 completion hook)
ALTER TABLE dms_task_events ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE dms_task_events ADD COLUMN IF NOT EXISTS trigger_text TEXT;
