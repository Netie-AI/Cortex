"""F6 skill capture tests."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "false")
    return db


@pytest.fixture
def loaded_state():
    return {
        "items": [
            {"id": f"item_{i}", "sku": f"SKU-{i:04d}", "location_id": "A1", "days_since_movement": 35}
            for i in range(5)
        ],
        "locations": [
            {"id": "A1", "capacity": 100, "occupied": 90},
            {"id": "A2", "capacity": 100, "occupied": 50},
        ],
        "recent_movements": [],
        "compliance_flags": [],
        "stale_days": 30,
    }


def _passing_event(ops_db, monkeypatch, *, task_id="audit_stale_items", enable_capture: bool = True):
    if enable_capture:
        monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "true")
    else:
        monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "false")
    from packs.dms.tasks.gate import check_task, create_task_event

    event_id = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id=task_id,
        intent="warehouse_audit",
        filled_template={"task_action": "audit", "value_myr": 100},
        actor="test",
        db_path=ops_db,
    )
    check_task(
        event_id,
        task_id,
        {"task_action": "audit", "value_myr": 100},
        db_path=ops_db,
    )
    return event_id


def test_capture_disabled_is_noop(ops_db, monkeypatch, loaded_state):
    from packs.dms.skills.capture import complete_event, list_skills

    event_id = _passing_event(ops_db, monkeypatch, enable_capture=False)
    result = complete_event(
        event_id,
        "success",
        trigger_text="please audit stale inventory items",
        actor="steward",
        db_path=ops_db,
    )
    assert result["captured"] is None
    assert list_skills() == []


def test_capture_only_on_success_and_consent(ops_db, monkeypatch):
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "true")
    from packs.dms.audit.ledger import list_entries
    from packs.dms.skills.capture import capture_from_event, complete_event, list_skills
    from packs.dms.tasks.gate import check_task, create_task_event

    # Failed gate — no capture
    fail_id = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id="send_quote_fail",
        intent="request_quote",
        filled_template={"task_action": "send_quote"},
        actor="test",
        db_path=ops_db,
    )
    check_task(fail_id, "send_quote_fail", {"task_action": "send_quote"}, db_path=ops_db)
    assert capture_from_event(fail_id, trigger_text="quote please", db_path=ops_db) is None

    # Pass gate but fail outcome — no capture
    pass_id = _passing_event(ops_db, monkeypatch)
    result = complete_event(pass_id, "abandoned", trigger_text="audit stale", db_path=ops_db)
    assert result["captured"] is None
    assert list_skills() == []

    # Pass gate + success — capture
    pass_id2 = _passing_event(ops_db, monkeypatch)
    trigger = "please audit stale inventory in warehouse zone a"
    result = complete_event(pass_id2, "success", trigger_text=trigger, actor="steward", db_path=ops_db)
    assert result["captured"] is not None
    skills = list_skills()
    assert len(skills) == 1
    assert skills[0]["task_id"] == "audit_stale_items"
    assert skills[0]["consented"] is True
    assert skills[0]["support_count"] >= 1

    ledger_types = [e.event_type for e in list_entries(db_path=ops_db)]
    assert "skill.captured" in ledger_types


def test_captured_skill_boosts_suggestion(ops_db, monkeypatch, loaded_state):
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "true")
    from packs.dms.skills.capture import complete_event, init_skills_schema
    from packs.dms.tasks.suggest import suggest
    import sqlite3

    event_id = _passing_event(ops_db, monkeypatch)
    trigger = "urgent audit stale inventory warehouse zone a"
    complete_event(event_id, "success", trigger_text=trigger, db_path=ops_db)

    without = suggest(loaded_state, trigger_text="unrelated random message")
    audit_plain = next(s for s in without if s["task_id"] == "audit_stale_items")
    base_conf = audit_plain["confidence"]
    assert "skill_match" not in audit_plain

    with_match = suggest(loaded_state, trigger_text=trigger)
    audit_boosted = next(s for s in with_match if s["task_id"] == "audit_stale_items")
    assert audit_boosted.get("skill_match", 0) >= 0.5
    assert audit_boosted["confidence"] > base_conf

    # Upsert increments support on second capture
    event_id2 = _passing_event(ops_db, monkeypatch)
    complete_event(event_id2, "success", trigger_text=trigger, db_path=ops_db)
    con = sqlite3.connect(str(ops_db))
    con.row_factory = sqlite3.Row
    init_skills_schema(con)
    row = con.execute(
        "SELECT support_count FROM dms_skills WHERE trigger_pattern = ?",
        ("urgent audit stale inventory warehouse zone a",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row["support_count"] >= 2


def test_deactivate_skill_excluded(ops_db, monkeypatch, loaded_state):
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "true")
    from packs.dms.skills.capture import complete_event, deactivate_skill
    from packs.dms.tasks.suggest import suggest

    event_id = _passing_event(ops_db, monkeypatch)
    trigger = "audit stale items now"
    result = complete_event(event_id, "success", trigger_text=trigger, db_path=ops_db)
    skill_id = result["captured"]["skill_id"]

    boosted = suggest(loaded_state, trigger_text=trigger)
    assert any(s.get("skill_match") for s in boosted if s["task_id"] == "audit_stale_items")

    assert deactivate_skill(skill_id, actor="steward") is True

    after = suggest(loaded_state, trigger_text=trigger)
    audit = next(s for s in after if s["task_id"] == "audit_stale_items")
    assert "skill_match" not in audit
