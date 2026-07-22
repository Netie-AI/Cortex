-- F7 role-aware RLS (Postgres/Supabase target).
-- App sets: SET app.tenant_id = '...'; SET app.role = 'viewer'|'steward'|'admin';
-- SQLite demo enforces tenant + role at the API layer; these policies apply on Postgres.

-- Extend audit ledger for tenant + visibility scoping
ALTER TABLE dms_audit_ledger
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

ALTER TABLE dms_audit_ledger
    ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'steward'
    CHECK (visibility IN ('viewer', 'steward'));

ALTER TABLE dms_audit_ledger ENABLE ROW LEVEL SECURITY;

-- Drop generic tenant-only policies from 001 (replaced by role-aware policies)
DROP POLICY IF EXISTS dms_locations_tenant_isolation ON dms_locations;
DROP POLICY IF EXISTS dms_items_tenant_isolation ON dms_items;
DROP POLICY IF EXISTS dms_movements_tenant_isolation ON dms_movements;

-- Helper: caller is within tenant
-- tenant_id = current_setting('app.tenant_id', true)

-- dms_locations
CREATE POLICY dms_locations_role_select ON dms_locations
    FOR SELECT
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('viewer', 'steward', 'admin')
    );

CREATE POLICY dms_locations_steward_insert ON dms_locations
    FOR INSERT
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

CREATE POLICY dms_locations_steward_update ON dms_locations
    FOR UPDATE
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

-- dms_items
CREATE POLICY dms_items_role_select ON dms_items
    FOR SELECT
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('viewer', 'steward', 'admin')
    );

CREATE POLICY dms_items_steward_insert ON dms_items
    FOR INSERT
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

CREATE POLICY dms_items_steward_update ON dms_items
    FOR UPDATE
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

-- dms_movements (steward-only writes; viewers may read within tenant)
CREATE POLICY dms_movements_role_select ON dms_movements
    FOR SELECT
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('viewer', 'steward', 'admin')
    );

CREATE POLICY dms_movements_steward_insert ON dms_movements
    FOR INSERT
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

-- dms_audit_ledger: viewers see viewer-visible entries; stewards/admins see all in tenant
CREATE POLICY dms_audit_ledger_role_select ON dms_audit_ledger
    FOR SELECT
    USING (
        tenant_id = current_setting('app.tenant_id', true)
        AND (
            current_setting('app.role', true) IN ('steward', 'admin')
            OR visibility = 'viewer'
        )
    );

CREATE POLICY dms_audit_ledger_steward_insert ON dms_audit_ledger
    FOR INSERT
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)
        AND current_setting('app.role', true) IN ('steward', 'admin')
    );

-- Append-only invariant preserved from 001 (no UPDATE/DELETE policies)
