"""
tests/dms/test_generative.py
Tests for packs.dms.generative.brain.
AI calls are mocked — tests verify governance, structure, and PII gate.
Run: pytest tests/dms/test_generative.py -q
"""
import json
import pytest
from unittest.mock import patch, MagicMock


# ─── Mock AI responses ────────────────────────────────────────────────────────

MOCK_CHART = {
    "chart_type": "bar",
    "title": "Inventory by Category",
    "x_key": "name",
    "y_keys": ["value"],
    "data": [{"name": "Electronics", "value": 150}, {"name": "Clothing", "value": 80}],
    "colors": ["#4F46E5", "#10B981"],
    "insights": ["Electronics dominates inventory."],
    "x_label": "Category",
    "y_label": "Units",
    "requires_confirm": False,
}

MOCK_EMAIL = {
    "subject": "Weekly Warehouse Summary",
    "body": "Dear [SENDER_NAME],\n\nThis week...\n\nRegards,\n[SENDER_NAME]",
    "to_suggestion": "CEO",
    "tone": "formal",
    "key_points": ["Inventory stable", "3 movements"],
    "requires_confirm": True,
    "review_note": "This draft requires your review and approval before sending.",
}

MOCK_ANALYSIS = {
    "period": "last_7_days",
    "narrative": "Warehouse operations were stable this week.",
    "key_findings": [{"finding": "Low movement", "significance": "medium", "metric": "5 moves"}],
    "recommendations": [{"action": "Audit slow items", "priority": "medium", "effort": "low"}],
    "risk_flags": [],
    "kpis": {"total_movements": 5, "items_received": 2, "items_shipped": 3, "compliance_events": 0, "space_utilization_pct": 65},
    "requires_confirm": False,
}

MOCK_CEO = {
    "title": "Warehouse Operations Executive Summary",
    "performance_score": 78,
    "executive_summary": "Operations are running efficiently.",
    "sections": [{"title": "Inventory", "content": "Stable.", "metrics": {}}],
    "top_actions": [{"action": "Review slow movers", "owner": "Ops Manager", "deadline": "This week", "priority": "medium"}],
    "requires_confirm": False,
}

MOCK_WHATSAPP = {
    "message": "Hi team, daily summary: 5 movements today. All clear.",
    "tone": "professional",
    "suggested_recipients": ["Warehouse Team"],
    "emoji_suggestion": "📦",
    "requires_confirm": True,
    "character_count": 51,
}

MOCK_SUMMARY = {"summary": "All inventory exported."}


def _mock_ai(intent: str) -> dict:
    """Return appropriate mock based on what prompt looks like."""
    mapping = {
        "chart": MOCK_CHART,
        "email": MOCK_EMAIL,
        "analysis": MOCK_ANALYSIS,
        "ceo": MOCK_CEO,
        "whatsapp": MOCK_WHATSAPP,
        "summary": MOCK_SUMMARY,
    }
    return mapping.get(intent, {})


# ─── Tests ────────────────────────────────────────────────────────────────────

@patch("packs.dms.generative.brain._ai", return_value=MOCK_CHART)
@patch("packs.dms.audit.ledger.append")
def test_generate_chart_returns_recharts_config(mock_ledger, mock_ai):
    from packs.dms.generative.brain import generate_chart
    result = generate_chart("show inventory", {"items": 100})
    assert result["chart_type"] in ("bar", "line", "area", "pie")
    assert "data" in result
    assert isinstance(result["data"], list)
    assert result["requires_confirm"] is False


@patch("packs.dms.generative.brain._ai", return_value=MOCK_EMAIL)
@patch("packs.dms.audit.ledger.append")
def test_draft_email_always_requires_confirm(mock_ledger, mock_ai):
    from packs.dms.generative.brain import draft_email
    result = draft_email("weekly summary for CEO", {})
    assert result["requires_confirm"] is True
    assert "subject" in result
    assert "body" in result


@patch("packs.dms.generative.brain._ai", return_value=MOCK_WHATSAPP)
@patch("packs.dms.audit.ledger.append")
def test_draft_whatsapp_always_requires_confirm(mock_ledger, mock_ai):
    from packs.dms.generative.brain import draft_whatsapp
    result = draft_whatsapp("send daily summary to staff", {})
    assert result["requires_confirm"] is True
    assert "message" in result
    assert "character_count" in result


@patch("packs.dms.generative.brain._ai", return_value=MOCK_ANALYSIS)
@patch("packs.dms.audit.ledger.append")
def test_analyze_sales_returns_structured_report(mock_ledger, mock_ai):
    from packs.dms.generative.brain import analyze_sales
    result = analyze_sales("last_7_days", {"dms_inventory": {"row_count": 100}})
    assert "narrative" in result
    assert "key_findings" in result
    assert "risk_flags" in result
    assert "kpis" in result
    assert result["requires_confirm"] is False


@patch("packs.dms.generative.brain._ai", return_value=MOCK_CEO)
@patch("packs.dms.audit.ledger.append")
def test_auto_analysis_has_performance_score(mock_ledger, mock_ai):
    from packs.dms.generative.brain import auto_analysis
    result = auto_analysis({"dms_inventory": {}})
    assert "performance_score" in result
    assert 0 <= result["performance_score"] <= 100
    assert result["requires_confirm"] is False
    assert "generated_at" in result


def test_export_csv_produces_valid_csv():
    """CSV export is deterministic — no AI mock needed."""
    from packs.dms.generative.brain import export_csv
    rows = [
        {"sku": "SKU-001", "qty": 10, "location": "A1"},
        {"sku": "SKU-002", "qty": 5, "location": "B2"},
    ]
    with patch("packs.dms.generative.brain._ai", return_value=MOCK_SUMMARY):
        result = export_csv("export all items", rows)
    assert result["row_count"] == 2
    assert result["columns"] == ["sku", "qty", "location"]
    assert "SKU-001" in result["csv_content"]
    assert result["filename"].endswith(".csv")
    assert result["requires_confirm"] is False


def test_export_csv_empty_rows():
    from packs.dms.generative.brain import export_csv
    result = export_csv("export nothing", [])
    assert result.get("error") is not None
    assert result["row_count"] == 0


# ─── Governance: PII gate in run() dispatcher ────────────────────────────────

@patch("packs.dms.generative.brain._ai", return_value=MOCK_EMAIL)
@patch("packs.dms.audit.ledger.append")
def test_pii_redacted_before_dispatch(mock_ledger, mock_ai):
    """PII in params must be redacted before reaching AI."""
    from packs.dms.generative.brain import run
    from packs.dms.security.pii import detect

    params = {"request": "Send email to Ahmad Farid at 011-2345678", "context": {}}
    run("draft_email", params, actor="test")

    call_args = mock_ai.call_args
    prompt_sent = call_args[0][0] if call_args[0] else ""
    assert detect("011-2345678") != []
    assert "011-2345678" not in prompt_sent


@patch("packs.dms.audit.ledger.append")
def test_run_always_writes_ledger(mock_ledger):
    from packs.dms.generative.brain import run
    with patch("packs.dms.generative.brain._ai", return_value=MOCK_CHART):
        run("generate_chart", {"query": "show inventory", "data": {}})
    mock_ledger.assert_called_once()
    args = mock_ledger.call_args[0]
    assert args[0] == "user"
    assert args[1] == "brain.invoked"


def test_unknown_intent_returns_error():
    from packs.dms.generative.brain import run
    result = run("teleport_items", {})
    assert "error" in result
    assert "teleport_items" in result["error"]
