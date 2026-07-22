# RLS proof (C-SEC-2 / F7 remainder) — design + CI wiring

**Status: DESIGN + TEST + CI job landed; green when migrations include `001`→`007`.**
Property: a `viewer` DB session cannot read steward-only or foreign-tenant audit-ledger rows.

## What proves it
- `packs/dms/sql/003_rls_policies.sql` — `dms_audit_ledger_role_select` (viewers see `visibility='viewer'` within tenant; stewards/admins see all in tenant).
- `packs/dms/sql/007_rls_ledger_force.sql` — `FORCE ROW LEVEL SECURITY` so the table owner is also subject (makes the deny observable in a single-connection test).
- `tests/dms/test_rls_blocks_out_of_scope_read.py` — asserts deny for out-of-scope role AND foreign tenant; allow for in-scope. **Skips (does not pass) without `DMS_LEDGER_DSN`.**

## App-layer contract (already in code)
Every request must stamp the session GUCs from the authenticated `api_auth.Caller` BEFORE any query:
```sql
SET app.tenant_id = '<caller tenant>';
SET app.role      = '<viewer|steward|admin>';
```
Never derive these from client-supplied fields — only from the resolved API key.

## CI job sketch (Cursor lands the YAML — B3)
```yaml
# .github/workflows/rls.yml
jobs:
  rls-proof:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: postgres, POSTGRES_DB: cortex }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready" --health-interval 10s
          --health-timeout 5s --health-retries 5
    env:
      DMS_LEDGER_DSN: postgresql+psycopg://postgres:postgres@localhost:5432/cortex
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dms,postgres,dev]" psycopg[binary]
      - run: pytest tests/dms/test_rls_blocks_out_of_scope_read.py -q
```

## Honesty
- Local suite (no DSN): test SKIPS with a reason — this is **PARTIAL**, not PASS.
- Gate flips to PASS only when the CI DSN job is green. Record in `STATUS.md` when it lands.

## Hand-back to Cursor (B3)
- Files to add: `.github/workflows/rls.yml` (above), `[postgres]` + `psycopg[binary]` to dev install.
- Do not weaken the test to pass without a DSN. Do not grant the app role BYPASSRLS.
