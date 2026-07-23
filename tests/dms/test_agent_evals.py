"""P16 agent-definition evals — scope containment against the ontology registry."""

from __future__ import annotations

from CortexOS.agent_sdk.evals import check_agent_scope, evaluate_agents

# AirGPT-style runtime tool surface (subset)
RUNTIME = ("vault_read", "rag_search", "rag_answer", "dms_query", "dms_action", "memory_search")


def test_well_scoped_agent_passes():
    report = check_agent_scope(
        {
            "name": "StockWatcher",
            "tools": ["dms_query", "dms_action"],
            "allowed_object_types": ["inventory", "alerts"],
            "allowed_action_types": ["export_pptx"],
        },
        pack="dms",
        runtime_tools=RUNTIME,
    )
    assert report.ok, report.violations
    # confirm-gated action surfaces as a note, not a violation
    assert any("requires human confirmation" in n for n in report.notes)


def test_unknown_references_are_violations():
    report = check_agent_scope(
        {
            "name": "Rogue",
            "tools": ["rm_rf", "dms_query"],
            "allowed_object_types": ["payroll"],
            "allowed_action_types": ["drop_tables"],
        },
        pack="dms",
        runtime_tools=RUNTIME,
    )
    assert not report.ok
    joined = " | ".join(report.violations)
    assert "rm_rf" in joined and "payroll" in joined and "drop_tables" in joined


def test_event_kind_action_is_not_invocable():
    report = check_agent_scope(
        {"name": "EventCaller", "allowed_action_types": ["item.intake"]},
        pack="dms",
        runtime_tools=RUNTIME,
    )
    assert not report.ok
    assert any("never invocable" in v for v in report.violations)


def test_registry_tool_counts_as_tool_without_runtime():
    # export_pptx is a registered kind:tool — valid even with no runtime surface
    report = check_agent_scope(
        {"name": "Minimal", "tools": ["export_pptx"]}, pack="dms", runtime_tools=()
    )
    assert report.ok, report.violations


def test_evaluate_agents_failures_sort_first():
    reports = evaluate_agents(
        [
            {"name": "Good", "tools": ["dms_query"]},
            {"name": "Bad", "tools": ["nuke_prod"]},
        ],
        pack="dms",
        runtime_tools=RUNTIME,
    )
    assert [r.agent for r in reports] == ["Bad", "Good"]
    assert not reports[0].ok and reports[1].ok
