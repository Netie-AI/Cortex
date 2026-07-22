-- F2 governed chat threads + messages (Postgres/Supabase target)
-- SQLite demo uses tenant_id column + app-layer enforcement; policies apply on Postgres.

CREATE TABLE IF NOT EXISTS dms_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_ref TEXT,
    customer_label TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dms_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL REFERENCES dms_threads(id),
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    lang TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dms_messages_thread
    ON dms_messages(thread_id, created_at);

ALTER TABLE dms_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE dms_messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY dms_threads_role_select ON dms_threads
    FOR SELECT
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('viewer', 'steward', 'admin')
    );

CREATE POLICY dms_threads_steward_insert ON dms_threads
    FOR INSERT
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

CREATE POLICY dms_threads_steward_update ON dms_threads
    FOR UPDATE
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

CREATE POLICY dms_messages_role_select ON dms_messages
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM dms_threads t
            WHERE t.id = dms_messages.thread_id
              AND t.tenant_id = current_setting('app.tenant_id', true)
        )
        AND current_setting('app.role', true) IN ('viewer', 'steward', 'admin')
    );

CREATE POLICY dms_messages_steward_insert ON dms_messages
    FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM dms_threads t
            WHERE t.id = dms_messages.thread_id
              AND t.tenant_id = current_setting('app.tenant_id', true)
        )
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );
