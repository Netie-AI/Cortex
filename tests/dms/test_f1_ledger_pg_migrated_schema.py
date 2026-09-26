"""GH-04 (#244): the Postgres ledger verifies on the schema a real deployment gets.

``001_warehouse_v0.sql`` creates ``dms_audit_ledger`` with ``payload JSONB`` and
``created_at TIMESTAMPTZ``. ``002_ledger_postgres.sql`` (the ledger's own schema,
TEXT columns) then no-ops on ``CREATE TABLE IF NOT EXISTS``. The first CI run
that actually executed the Postgres ledger tests failed exactly here:
``verify()`` called ``json.loads`` on a JSONB dict, and a TIMESTAMPTZ read back
as a ``datetime`` can never match the ISO string that was hashed.

This test migrates in order into an isolated schema with a non-UTC session time
zone and asserts on what a caller sees: ``verify()`` and ``list_entries``.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest

DSN = os.environ.get("DMS_LEDGER_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="DMS_LEDGER_DSN unset: Postgres ledger proof NOT run")

SQL_DIR = Path(__file__).resolve().parents[2] / "packs" / "dms" / "sql"
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


def test_postgres_migrated_schema_chain_verifies(monkeypatch):
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from packs.dms.audit import ledger

    schema = f"ledger_mig_{uuid.uuid4().hex[:8]}"
    admin = sqlalchemy.create_engine(DSN)
    with admin.begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
    try:
        opts = quote(f"-csearch_path={schema},public -ctimezone=Asia/Kuala_Lumpur")
        dsn = f"{DSN}{'&' if '?' in DSN else '?'}options={opts}"
        scoped = sqlalchemy.create_engine(dsn)
        with scoped.begin() as conn:
            for name in ("001_warehouse_v0.sql", "002_ledger_postgres.sql"):
                conn.exec_driver_sql((SQL_DIR / name).read_text(encoding="utf-8"))
            types = dict(
                conn.exec_driver_sql(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = %(s)s AND table_name = 'dms_audit_ledger' "
                    "AND column_name IN ('payload', 'created_at')",
                    {"s": schema},
                ).all()
            )
        scoped.dispose()
        # The deployment shape this test exists for: 001's types survive 002.
        assert types == {"payload": "jsonb", "created_at": "timestamp with time zone"}

        monkeypatch.setenv("DMS_LEDGER_DSN", dsn)
        monkeypatch.setattr(ledger, "_pg_schema_ready", False)
        payloads = [
            {"n": 1.5, "s": "Kuala Lumpur é", "nested": {"b": 2, "a": [1, 2]}},
            {"qty": 12, "ok": True, "none": None},
            {"sku": "SKU-BETA"},
        ]
        for p in payloads:
            ledger.append("migrated-schema", "ledger.proof", p)

        result = ledger.verify()
        assert result.ok is True, f"chain broken at seq {result.broken_at}"
        entries = ledger.list_entries(limit=10)
        assert [e.seq for e in entries] == [0, 1, 2]
        assert [e.payload for e in entries] == payloads
        for e in entries:
            assert ISO_UTC.match(e.created_at), e.created_at
    finally:
        with admin.begin() as conn:
            conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()
