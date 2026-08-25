"""F1 tamper-evident audit ledger tests."""

from __future__ import annotations

import concurrent.futures
import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEDGER_MIGRATION = ROOT / "packs" / "dms" / "sql" / "002_ledger_postgres.sql"
POSTGRES_DSN = os.environ.get("DMS_LEDGER_DSN")


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / "ledger.db"
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def test_chain_append_and_verify_ok(ledger_db):
    from packs.dms.audit.ledger import append, verify

    for i in range(100):
        append("tester", "smoke.event", {"n": i}, db_path=ledger_db)

    result = verify(db_path=ledger_db)
    assert result.ok is True
    assert result.broken_at is None


def test_verify_past_the_tip_fails_closed(ledger_db):
    from packs.dms.audit.ledger import append, verify

    append("tester", "seed", {"x": 1}, db_path=ledger_db)
    result = verify(db_path=ledger_db, start_seq=99)
    assert result.ok is False
    assert result.broken_at == 99


def test_verify_seq_gap_fails_closed(ledger_db):
    from packs.dms.audit.ledger import append, verify

    append("tester", "seed", {"x": 0}, db_path=ledger_db)
    append("tester", "seed", {"x": 1}, db_path=ledger_db)
    con = sqlite3.connect(str(ledger_db))
    try:
        con.execute("DELETE FROM dms_audit_ledger WHERE seq = 0")
        con.commit()
    finally:
        con.close()
    result = verify(db_path=ledger_db)
    assert result.ok is False
    assert result.broken_at is not None


def test_tamper_detected(ledger_db):
    from packs.dms.audit.ledger import append, verify

    append("tester", "seed", {"x": 1}, db_path=ledger_db)
    append("tester", "seed", {"x": 2}, db_path=ledger_db)

    con = sqlite3.connect(str(ledger_db))
    try:
        con.execute(
            "UPDATE dms_audit_ledger SET payload = ? WHERE seq = 1",
            ('{"x": 999}',),
        )
        con.commit()
    finally:
        con.close()

    result = verify(db_path=ledger_db)
    assert result.ok is False
    assert result.broken_at == 1


def test_concurrent_appends_consistent(ledger_db):
    from packs.dms.audit.ledger import append, verify

    def _append(n: int) -> None:
        append("worker", "concurrent.event", {"n": n}, db_path=ledger_db)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(_append, range(20)))

    result = verify(db_path=ledger_db)
    assert result.ok is True

    con = sqlite3.connect(str(ledger_db))
    try:
        count = con.execute("SELECT COUNT(*) FROM dms_audit_ledger").fetchone()[0]
        max_seq = con.execute("SELECT MAX(seq) FROM dms_audit_ledger").fetchone()[0]
    finally:
        con.close()

    assert count == 20
    assert max_seq == 19


@pytest.fixture
def postgres_ledger(monkeypatch):
    if not POSTGRES_DSN:
        pytest.skip("DMS_LEDGER_DSN not set")

    pytest.importorskip("sqlalchemy")
    from sqlalchemy import create_engine, text

    engine = create_engine(POSTGRES_DSN, pool_pre_ping=True)
    sql = LEDGER_MIGRATION.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
        conn.execute(text("TRUNCATE dms_audit_ledger"))

    monkeypatch.setenv("DMS_LEDGER_DSN", POSTGRES_DSN)
    yield engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE dms_audit_ledger"))
    engine.dispose()


def test_postgres_chain_append_and_verify_ok(postgres_ledger):
    from packs.dms.audit.ledger import append, verify

    for i in range(50):
        append("pg-tester", "postgres.smoke", {"n": i})

    result = verify()
    assert result.ok is True
    assert result.broken_at is None


def test_postgres_concurrent_appends_consistent(postgres_ledger):
    from packs.dms.audit.ledger import append, verify

    def _append(n: int) -> None:
        append("pg-worker", "postgres.concurrent", {"n": n})

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(_append, range(20)))

    result = verify()
    assert result.ok is True

    from sqlalchemy import text

    with postgres_ledger.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM dms_audit_ledger")).scalar()
        max_seq = conn.execute(text("SELECT MAX(seq) FROM dms_audit_ledger")).scalar()

    assert count == 20
    assert max_seq == 19


def test_postgres_append_only_trigger(postgres_ledger):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packs.dms.audit.ledger import append

    append("pg-tester", "immutable.check", {"ok": True})

    with postgres_ledger.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text("UPDATE dms_audit_ledger SET payload = :p WHERE seq = 0"),
                {"p": '{"ok": false}'},
            )
            conn.commit()
