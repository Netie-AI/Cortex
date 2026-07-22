-- V0 warehouse tables + RLS policies (Supabase/Postgres target)
-- SQLite demo uses tenant_id column + app-layer enforcement; policies apply on Postgres.

CREATE TABLE IF NOT EXISTS dms_locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id UUID REFERENCES dms_locations(id),
    kind TEXT NOT NULL CHECK (kind IN ('zone', 'rack', 'bin')),
    code TEXT NOT NULL UNIQUE,
    qr_token TEXT NOT NULL UNIQUE,
    capacity_volume NUMERIC,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dms_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku TEXT NOT NULL,
    label TEXT NOT NULL,
    current_location_id UUID REFERENCES dms_locations(id),
    photo_uri TEXT,
    dims JSONB,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dms_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES dms_items(id),
    from_location_id UUID REFERENCES dms_locations(id),
    to_location_id UUID NOT NULL REFERENCES dms_locations(id),
    actor TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('scan', 'manual')),
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE dms_locations ENABLE ROW LEVEL SECURITY;
ALTER TABLE dms_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE dms_movements ENABLE ROW LEVEL SECURITY;

CREATE POLICY dms_locations_tenant_isolation ON dms_locations
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY dms_items_tenant_isolation ON dms_items
    USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY dms_movements_tenant_isolation ON dms_movements
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Append-only audit ledger (F1)
CREATE TABLE IF NOT EXISTS dms_audit_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seq BIGINT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    prev_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signature TEXT
);

REVOKE UPDATE, DELETE ON dms_audit_ledger FROM PUBLIC;

CREATE OR REPLACE FUNCTION dms_audit_ledger_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'dms_audit_ledger is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER dms_audit_ledger_no_update
    BEFORE UPDATE OR DELETE ON dms_audit_ledger
    FOR EACH ROW EXECUTE FUNCTION dms_audit_ledger_immutable();
