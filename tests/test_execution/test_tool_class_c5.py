"""C5-min — ontology tool_class tags and agent→apply refusal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution.tool_runner import ToolCallError, is_agent_actor, run_tool_call
from CortexOS.ontology.registry import load_tool_specs, resolve_tool_class
from packs.dms.ontology.registry import PACK_DIR


@pytest.fixture(autouse=True)
def _pack_dms(monkeypatch):
    monkeypatch.setenv("PACK", "dms")


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / "ledger.db"
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def test_tool_class_enum_values():
    from cortex_contract.tools import ToolClass

    assert {c.value for c in ToolClass} == {"read", "propose", "apply"}


def test_export_pptx_tagged_apply_in_ontology():
    tools = {t.id: t for t in load_tool_specs(PACK_DIR)}
    assert "export_pptx" in tools
    assert tools["export_pptx"].tool_class == "apply"


def test_is_agent_actor_prefix():
    assert is_agent_actor("agent_steward")
    assert is_agent_actor("agent_viewer")
    assert not is_agent_actor("steward")
    assert not is_agent_actor("api_steward")


def test_agent_cannot_invoke_apply_tool(ledger_db, tmp_path, monkeypatch):
    monkeypatch.setattr("CortexOS.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    with pytest.raises(ToolCallError) as exc:
        run_tool_call(
            "export_pptx",
            {"title": "Blocked"},
            actor="agent_steward",
            run_id="agent1",
            db_path=ledger_db,
        )
    assert exc.value.verdict == "agent_apply_denied"


def test_human_steward_can_invoke_apply_tool(ledger_db, tmp_path, monkeypatch):
    monkeypatch.setattr("CortexOS.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    result = run_tool_call(
        "export_pptx",
        {"title": "Allowed", "body": "demo"},
        actor="steward",
        run_id="human1",
        db_path=ledger_db,
    )
    assert result["ok"] is True
    assert result["verdict"] == "pass"


def test_resolve_tool_class_reads_compiled_db(tmp_path):
    from packs.dms.ontology import registry

    db = tmp_path / "ops.db"
    registry.compile_to_sqlite(PACK_DIR, db)
    assert resolve_tool_class("export_pptx", db_path=db, pack_dir=PACK_DIR) == "apply"


@pytest.fixture
def contract_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_contract_tool_registry_returns_ontology_tool_class(contract_client):
    res = contract_client.get("/v1/contract/tools")
    assert res.status_code == 200
    tools = {t["id"]: t for t in res.json()["tools"]}
    assert tools["export_pptx"]["class_name"] == "apply"
    assert tools["export_pptx"]["description"]
