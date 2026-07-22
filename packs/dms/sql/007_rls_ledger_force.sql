-- C-SEC-2 (F7 remainder) — make the audit-ledger RLS policy provable.
--
-- 003 ENABLEs RLS + defines dms_audit_ledger_role_select (viewers see only
-- visibility='viewer' rows within their tenant). But ENABLE alone lets the table
-- OWNER bypass RLS, so a single-connection proof can't demonstrate the deny.
-- FORCE subjects the owner to the policies too — now `SET app.role='viewer'`
-- genuinely cannot read steward-only rows, which is exactly the property the
-- RLS proof test asserts. Additive + idempotent.
ALTER TABLE dms_audit_ledger FORCE ROW LEVEL SECURITY;
