"""F6 skill capture tests."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def _passing_event(ops_db, *, intent="request_quote", task_id="send_quote_skill"):
    from packs.dms.tasks.gate import check_task, create_task_event

    template = {
        "task_action": "send_quote",
        "quote_total_myr": 900,
        "value_myr": 900,
        "human_acknowledged": True,
    }
    event_id = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id=task_id,
        intent=intent,
        filled_template=template,
        actor="test",
        db_path=ops_db,
    )
    check_task(event_id, task_id, template, db_path=ops_db)
    return event_id, task_id


def test_capture_only_on_success_and_consent(ops_db, monkeypatch):
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "1")
    from packs.dms.audit.ledger import list_entries
    from packs.dms.skills.capture import capture_from_event, list_skills

    event_id, task_id = _passing_event(ops_db)
    trigger = "Please send quote for pallet shipment today"

    captured = capture_from_event(
        event_id,
        trigger_text=trigger,
        outcome="success",
        actor="steward",
        db_path=ops_db,
    )
    assert captured is not None
    assert captured["task_id"] == task_id

    skills = list_skills(db_path=ops_db)
    assert len(skills) == 1
    assert skills[0]["consented"] is True
    assert skills[0]["support_count"] == 1

    entries = list_entries(db_path=ops_db)
    assert any(e.event_type == "skill.captured" for e in entries)

    noop = capture_from_event(
        event_id,
        trigger_text=trigger,
        outcome="failed",
        actor="steward",
        db_path=ops_db,
    )
    assert noop is None

    from packs.dms.tasks.gate import check_task, create_task_event

    fail_event = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id="blocked_quote",
        intent="request_quote",
        filled_template={"task_action": "send_quote"},
        actor="test",
        db_path=ops_db,
    )
    check_task(fail_event, "blocked_quote", {"task_action": "send_quote"}, db_path=ops_db)
    blocked = capture_from_event(
        fail_event,
        trigger_text="blocked quote request",
        outcome="success",
        actor="steward",
        db_path=ops_db,
    )
    assert blocked is None
    assert len(list_skills(db_path=ops_db)) == 1


def test_capture_disabled_is_noop(ops_db, monkeypatch):
    monkeypatch.delenv("DMS_SKILL_CAPTURE_ENABLED", raising=False)
    from packs.dms.skills.capture import capture_from_event, list_skills

    event_id, _ = _passing_event(ops_db)
    captured = capture_from_event(
        event_id,
        trigger_text="quote for urgent delivery",
        outcome="success",
        actor="steward",
        db_path=ops_db,
    )
    assert captured is None
    assert list_skills(db_path=ops_db) == []


def test_captured_skill_boosts_suggestion(ops_db, monkeypatch):
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "1")
    from packs.dms.skills.capture import capture_from_event
    from packs.dms.tasks.suggest import suggest

    event_id, _ = _passing_event(
        ops_db,
        intent="warehouse_audit",
        task_id="audit_stale_items",
    )
    trigger = "stale inventory audit needed for slow moving stock"
    capture_from_event(
        event_id,
        trigger_text=trigger,
        outcome="success",
        actor="steward",
        db_path=ops_db,
    )

    loaded_state = {
        "items": [
            {"id": f"item_{i}", "sku": f"SKU-{i:04d}", "location_id": "A1", "days_since_movement": 35}
            for i in range(5)
        ],
        "locations": [{"id": "A1", "capacity": 100, "occupied": 90}],
        "recent_movements": [],
        "compliance_flags": [],
        "stale_days": 30,
    }

    baseline = suggest(loaded_state, trigger_text="unrelated warehouse check")
    boosted = suggest(loaded_state, trigger_text=trigger)

    base_audit = next(s for s in baseline if s["task_id"] == "audit_stale_items")
    boosted_audit = next(s for s in boosted if s["task_id"] == "audit_stale_items")
    assert boosted_audit.get("skill_match") is not None
    assert boosted_audit["confidence"] >= base_audit["confidence"]


def test_deactivate_skill_excluded(ops_db, monkeypatch):
    monkeypatch.setenv("DMS_SKILL_CAPTURE_ENABLED", "1")
    from packs.dms.skills.capture import capture_from_event, deactivate_skill, list_skills
    from packs.dms.tasks.suggest import suggest

    event_id, _ = _passing_event(
        ops_db,
        intent="warehouse_audit",
        task_id="audit_stale_items",
    )
    trigger = "stale inventory audit needed for slow moving stock"
    captured = capture_from_event(
        event_id,
        trigger_text=trigger,
        outcome="success",
        actor="steward",
        db_path=ops_db,
    )
    assert captured is not None
    deactivate_skill(captured["skill_id"], actor="steward", db_path=ops_db)

    active = [s for s in list_skills(db_path=ops_db) if s["active"]]
    assert active == []

    loaded_state = {
        "items": [
            {"id": f"item_{i}", "sku": f"SKU-{i:04d}", "location_id": "A1", "days_since_movement": 35}
            for i in range(5)
        ],
        "locations": [],
        "recent_movements": [],
        "compliance_flags": [],
        "stale_days": 30,
    }
    result = suggest(loaded_state, trigger_text=trigger)
    audit = next(s for s in result if s["task_id"] == "audit_stale_items")
    assert audit.get("skill_match") is None
