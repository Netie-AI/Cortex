"""F2 governed chat smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "packs" / "dms" / "sql" / "004_chat_v0.sql"


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def test_create_thread_append_message_and_ledger(ops_db):
    from packs.dms.audit.ledger import list_entries
    from packs.dms.chat import threads

    created = threads.create_thread(
        external_ref="wa:+60123456789",
        customer_label="Demo Customer",
        actor="fde",
        db_path=ops_db,
    )
    thread = created["thread"]
    assert thread["id"]
    assert thread["status"] == "open"
    assert created["ledger_seq"] == 0

    appended = threads.append_message(
        thread_id=thread["id"],
        sender="customer",
        body="Where is my shipment?",
        actor="fde",
        db_path=ops_db,
    )
    message = appended["message"]
    assert message["thread_id"] == thread["id"]
    assert message["direction"] == "inbound"
    assert message["body"] == "Where is my shipment?"
    assert appended["ledger_seq"] == 1

    messages = threads.list_messages(thread["id"], db_path=ops_db)
    assert len(messages) == 1
    assert messages[0]["id"] == message["id"]

    entries = list_entries(db_path=ops_db)
    assert len(entries) == 2
    assert entries[0].event_type == "thread.created"
    assert entries[0].payload["thread_id"] == thread["id"]
    assert entries[1].event_type == "message.inbound"
    assert entries[1].payload["message_id"] == message["id"]


def test_chat_api_routes(ops_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import netie.config
    from CortexOS.api.app import create_app

    netie.config._cached_config = None
    import os

    os.environ["PACK"] = "dms"
    netie.config._cached_config = None
    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/dms/threads",
        json={"external_ref": "email:test@example.com", "customer_label": "Test Co"},
    )
    assert res.status_code == 200
    thread_id = res.json()["thread"]["id"]

    res = client.post(
        f"/dms/threads/{thread_id}/messages",
        json={"sender": "customer", "body": "Need stock count for SKU-99"},
    )
    assert res.status_code == 200
    assert res.json()["message"]["body"] == "Need stock count for SKU-99"

    res = client.get(f"/dms/threads/{thread_id}/messages")
    assert res.status_code == 200
    assert len(res.json()["messages"]) == 1


def test_postgres_migration_present():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "dms_threads" in sql
    assert "dms_messages" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
