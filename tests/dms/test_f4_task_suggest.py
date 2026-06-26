"""
tests/dms/test_f4_task_suggest.py
F4 task suggest tests.
Run: pytest tests/dms/test_f4_task_suggest.py -q
"""
import json
import pytest
from unittest.mock import patch


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def empty_state():
    return {"items": [], "locations": [], "recent_movements": [], "compliance_flags": []}


@pytest.fixture
def loaded_state():
    return {
        "items": [
            {"id": f"item_{i}", "sku": f"SKU-{i:04d}", "location_id": "A1", "days_since_movement": 35}
            for i in range(5)
        ],
        "locations": [
            {"id": "A1", "capacity": 100, "occupied": 90},  # overloaded
            {"id": "A2", "capacity": 100, "occupied": 50},
        ],
        "recent_movements": [
            {"id": f"mv_{i}", "item_id": "item_0", "to_location": "A2", "minutes_ago": 10}
            for i in range(12)  # 12 movements in last hour
        ],
        "compliance_flags": [
            {"id": "flag_1", "type": "oversize_unconfirmed", "description": "Item exceeds bin limit.", "item_id": "item_0"}
        ],
        "stale_days": 30,
    }


# ─── Rule candidates ──────────────────────────────────────────────────────────

def test_empty_state_returns_no_suggestions(empty_state):
    from packs.dms.tasks.suggest import suggest
    result = suggest(empty_state)
    assert result == []


def test_stale_items_trigger_audit_suggestion(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    task_ids = [s["task_id"] for s in suggestions]
    assert "audit_stale_items" in task_ids


def test_overloaded_location_triggers_rebalance(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    task_ids = [s["task_id"] for s in suggestions]
    assert "rebalance_overloaded" in task_ids


def test_compliance_flag_surfaces_as_critical(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    critical = [s for s in suggestions if s["priority"] == "critical"]
    assert len(critical) >= 1
    assert critical[0]["source"].startswith("rule:compliance")


def test_batch_confirm_triggers_on_high_movement(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    task_ids = [s["task_id"] for s in suggestions]
    assert "batch_confirm_movements" in task_ids


def test_all_suggestions_require_confirm(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    assert all(s["requires_confirm"] is True for s in suggestions)


def test_suggestions_have_confidence_and_timestamp(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    for s in suggestions:
        assert "confidence" in s
        assert 0.0 <= s["confidence"] <= 1.0
        assert "suggested_at" in s


def test_compliance_flag_has_highest_confidence(loaded_state):
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(loaded_state)
    compliance = [s for s in suggestions if "compliance" in s["source"]]
    assert compliance[0]["confidence"] == 1.0


def test_max_10_suggestions_returned(loaded_state):
    """Never return more than 10 suggestions."""
    # Add lots of flags
    state = {**loaded_state, "compliance_flags": [
        {"id": f"f{i}", "type": "test", "description": "flag", "item_id": f"item_{i}"}
        for i in range(20)
    ]}
    from packs.dms.tasks.suggest import suggest
    suggestions = suggest(state)
    assert len(suggestions) <= 10


# ─── record_choice / record_outcome ──────────────────────────────────────────

def test_record_choice_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "test.db"))
    from packs.dms.tasks.suggest import record_choice, record_outcome
    # Should not raise even on fresh DB
    record_choice("audit_stale_items", True, "test_user")
    record_outcome("audit_stale_items", "success")


def test_batch_stats_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "test.db"))
    from packs.dms.tasks.suggest import record_choice
    from packs.dms.tasks.learn import refresh_stats, get_stats
    # Record some choices
    record_choice("task_a", True)
    record_choice("task_a", True)
    record_choice("task_a", False)
    record_choice("task_b", False)
    result = refresh_stats()
    assert result["ok"] is True
    assert result["tasks_updated"] >= 1
    stats = get_stats("task_a")
    assert len(stats) == 1
    assert stats[0]["total_shown"] == 3
    assert stats[0]["total_accepted"] == 2
    assert abs(stats[0]["accept_rate"] - 0.667) < 0.01
