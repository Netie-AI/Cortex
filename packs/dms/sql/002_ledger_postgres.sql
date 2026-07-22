-- F1 tamper-evident audit ledger (Postgres / Supabase)
-- Column semantics match SQLite ops DB (packs/dms/audit/ledger.py).

CREATE TABLE IF NOT EXISTS dms_audit_ledger (
    id TEXT PRIMARY KEY,
    seq BIGINT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    signature TEXT
);

CREATE INDEX IF NOT EXISTS idx_dms_audit_seq ON dms_audit_ledger(seq);

-- Append-only enforcement at the database layer.
REVOKE UPDATE, DELETE ON dms_audit_ledger FROM PUBLIC;

CREATE OR REPLACE FUNCTION dms_audit_ledger_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'dms_audit_ledger is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS dms_audit_ledger_no_update ON dms_audit_ledger;

CREATE TRIGGER dms_audit_ledger_no_update
    BEFORE UPDATE OR DELETE ON dms_audit_ledger
    FOR EACH ROW EXECUTE FUNCTION dms_audit_ledger_immutable();

-- Concurrent append serialization (application layer):
--   packs/dms/audit/ledger.py calls pg_advisory_xact_lock(<key>) at the start of
--   each append transaction before reading the tail row. The lock is released on
--   COMMIT/ROLLBACK, so concurrent writers queue safely and seq/prev_hash stay gap-free.
--   Lock key (signed int64): lower 63 bits of sha256('dms_audit_ledger'), see ledger.py.
-- Alternative: SELECT seq, entry_hash FROM dms_audit_ledger ORDER BY seq DESC LIMIT 1 FOR UPDATE
--   (requires an existing tail row; advisory lock also covers the empty-ledger genesis case).
