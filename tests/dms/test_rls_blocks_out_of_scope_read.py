"""C-SEC-2 — Postgres RLS proof: a viewer session cannot read steward-only rows.

Runs ONLY when DMS_LEDGER_DSN points at a Postgres instance. Without a DSN the
test SKIPS with a reason — it must never false-green (skip != pass). CI wires the
DSN via a Postgres service container (see docs/security/RLS_PROOF.md).

Property proven (fail-closed):
  * app.role='viewer'  → SELECT returns ONLY visibility='viewer' rows in tenant.
  * app.role='steward' → SELECT returns steward + viewer rows.
  * a foreign tenant_id is invisible regardless of role.
"""
from __future__ import annotations

import os
import uuid

import pytest

DSN = os.environ.get("DMS_LEDGER_DSN", "").strip()
pytestmark = pytest.mark.skipif(
    not DSN, reason="DMS_LEDGER_DSN not set — RLS proof requires a live Postgres (skip != pass)"
)

# 001 creates warehouse tables that 003's policies reference; 002 is IF NOT EXISTS
# for the ledger (no-op when 001 already created it) + append-only trigger hygiene.
MIGRATIONS = (
    "001_warehouse_v0.sql",
    "002_ledger_postgres.sql",
    "003_rls_policies.sql",
    "007_rls_ledger_force.sql",
)


def _apply_migrations(conn):
    from pathlib import Path

    sql_dir = Path(__file__).resolve().parents[2] / "packs" / "dms" / "sql"
    for name in MIGRATIONS:
        conn.exec_driver_sql((sql_dir / name).read_text(encoding="utf-8"))


def _insert(conn, *, seq, visibility, tenant):
    conn.exec_driver_sql(
        "INSERT INTO dms_audit_ledger "
        "(id, seq, actor, event_type, payload, prev_hash, entry_hash, created_at, signature,"
        " tenant_id, visibility) "
        "VALUES (%s,%s,'system','test','{}'::jsonb, repeat('0',64), %s, now(), NULL, %s, %s)",
        (str(uuid.uuid4()), seq, f"{seq:064d}", tenant, visibility),
    )


@pytest.fixture
def engine():
    sa = pytest.importorskip("sqlalchemy")
    eng = sa.create_engine(DSN, pool_pre_ping=True)
    yield eng
    eng.dispose()


def test_rls_blocks_out_of_scope_read(engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        _apply_migrations(conn)
        # seed as admin so the insert WITH CHECK passes
        conn.exec_driver_sql("SET app.tenant_id = 'default'")
        conn.exec_driver_sql("SET app.role = 'admin'")
        base = 900000
        _insert(conn, seq=base + 1, visibility="steward", tenant="default")
        _insert(conn, seq=base + 2, visibility="viewer", tenant="default")
        _insert(conn, seq=base + 3, visibility="viewer", tenant="other")  # foreign tenant

    with engine.connect() as conn:
        conn.exec_driver_sql("SET app.tenant_id = 'default'")

        conn.exec_driver_sql("SET app.role = 'viewer'")
        viewer_rows = conn.execute(text(
            "SELECT visibility, tenant_id FROM dms_audit_ledger "
            "WHERE seq BETWEEN 900001 AND 900003")).fetchall()
        vis = {(r[0], r[1]) for r in viewer_rows}
        assert ("steward", "default") not in vis          # deny: out-of-scope role
        assert ("viewer", "other") not in vis             # deny: foreign tenant
        assert ("viewer", "default") in vis               # allow: in-scope

        conn.exec_driver_sql("SET app.role = 'steward'")
        steward_rows = conn.execute(text(
            "SELECT visibility FROM dms_audit_ledger "
            "WHERE seq BETWEEN 900001 AND 900003 AND tenant_id='default'")).fetchall()
        assert {r[0] for r in steward_rows} == {"steward", "viewer"}  # allow: both
