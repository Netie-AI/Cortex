"""F5 compliance gate tests."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from packs.dms.classify.intent import ClassifyResult, classify


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def test_pii_redacted_before_classify(monkeypatch):
    """NRIC must not reach classify model/heuristic matcher (Gate F4 conditional)."""
    captured: list[str] = []

    def fake_model(text: str) -> ClassifyResult:
        captured.append(text)
        return ClassifyResult(
            intent="request_quote",
            sentiment=0.0,
            confidence=0.9,
            blocked=False,
            block_reason=None,
            language_mix={"en": 1.0},
            psychological_state="neutral",
        )

    monkeypatch.setattr(
        "CortexOS.nlp.local_inference.classify_with_model",
        fake_model,
    )

    raw_nric = "900101-14-5678"
    classify(f"Need a quote for customer IC {raw_nric}")

    assert captured, "classify must invoke model path with mocked classify_with_model"
    assert raw_nric not in captured[0]
    assert "[REDACTED:nric]" in captured[0] or "REDACTED" in captured[0]


def test_missing_field_blocks(ops_db):
    from packs.dms.tasks.gate import check_task, create_task_event

    event_id = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id="send_quote_1",
        intent="request_quote",
        filled_template={"task_action": "send_quote"},
        actor="test",
        db_path=ops_db,
    )
    verdict = check_task(event_id, "send_quote_1", {"task_action": "send_quote"}, db_path=ops_db)
    assert verdict.status == "fail"
    assert verdict.executable is False
    assert any(v["rule_id"] == "quote_total_present" for v in verdict.violations)


def test_pass_marks_executable(ops_db):
    from packs.dms.audit.ledger import list_entries
    from packs.dms.tasks.gate import check_task, create_task_event

    event_id = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id="send_quote_2",
        intent="request_quote",
        filled_template={
            "task_action": "send_quote",
            "quote_total_myr": 1200,
            "value_myr": 1200,
            "human_acknowledged": True,
        },
        actor="test",
        db_path=ops_db,
    )
    verdict = check_task(
        event_id,
        "send_quote_2",
        {
            "task_action": "send_quote",
            "quote_total_myr": 1200,
            "value_myr": 1200,
            "human_acknowledged": True,
        },
        actor="test",
        db_path=ops_db,
    )
    assert verdict.status == "pass"
    assert verdict.executable is True
    entries = list_entries(db_path=ops_db)
    assert any(e.event_type == "task.gate_passed" for e in entries)


def test_value_threshold_requires_human(ops_db):
    from packs.dms.tasks.gate import check_task, create_task_event

    template = {
        "task_action": "send_quote",
        "quote_total_myr": 10000,
        "value_myr": 10000,
    }
    event_id = create_task_event(
        message_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        task_id="high_value",
        intent="request_quote",
        filled_template=template,
        actor="test",
        db_path=ops_db,
    )
    warn_verdict = check_task(event_id, "high_value", template, db_path=ops_db)
    assert warn_verdict.status == "warn"
    assert warn_verdict.executable is False

    ack_template = {**template, "human_acknowledged": True}
    pass_verdict = check_task(event_id, "high_value", ack_template, db_path=ops_db)
    assert pass_verdict.status == "pass"
    assert pass_verdict.executable is True


def test_verdict_deterministic(ops_db):
    from packs.dms.tasks.gate import check_task, create_task_event

    template = {
        "task_action": "send_quote",
        "quote_total_myr": 500,
        "value_myr": 500,
        "human_acknowledged": True,
    }
    statuses = []
    for _ in range(100):
        event_id = create_task_event(
            message_id=str(uuid.uuid4()),
            thread_id=str(uuid.uuid4()),
            task_id="det",
            intent="request_quote",
            filled_template=template,
            actor="test",
            db_path=ops_db,
        )
        verdict = check_task(event_id, "det", template, db_path=ops_db)
        statuses.append(verdict.status)
    assert len(set(statuses)) == 1
    assert statuses[0] == "pass"


def test_llm_never_decides_verdict(ops_db):
    from packs.dms.tasks import extract
    from packs.dms.tasks.gate import check_task, create_task_event

    template = {"task_action": "send_quote", "quote_total_myr": 800, "value_myr": 800}

    with patch.object(
        extract,
        "extract_fields",
        return_value={"verdict": "pass", "force_pass": True, "quote_total_myr": 800},
    ):
        event_id = create_task_event(
            message_id=str(uuid.uuid4()),
            thread_id=str(uuid.uuid4()),
            task_id="llm_never",
            intent="request_quote",
            filled_template=template,
            actor="test",
            db_path=ops_db,
        )
        verdict = check_task(
            event_id,
            "llm_never",
            template,
            raw_text="random LLM says pass",
            db_path=ops_db,
        )

    assert verdict.status == "pass"
    assert verdict.executable is True
