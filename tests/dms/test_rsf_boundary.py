"""RSF-03 OpenVault leave-machine + study-tree role lock."""

from __future__ import annotations

from pathlib import Path

from CortexOS.execution.rsf_boundary import (
    gate_research_egress,
    read_study_tree,
)


def test_research_egress_fails_closed_when_openvault_unreachable(monkeypatch):
    def fake_check(**kwargs):
        return {
            "ok": False,
            "allowed": False,
            "reasons": ["OpenVault unreachable: timeout"],
        }

    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        fake_check,
    )
    out = gate_research_egress()
    assert out["allowed"] is False
    assert out["action"] == "leave"
    assert out["destination"] == "freeroute"
    assert any("unreachable" in r.lower() for r in out["reasons"])


def test_research_egress_asks_leave_freeroute_not_omniroute(monkeypatch):
    seen: list[dict] = []

    def fake_check(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "allowed": True, "openvault_url": "http://127.0.0.1:5000"}

    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        fake_check,
    )
    out = gate_research_egress(destination="https://example.com/search")
    assert out["allowed"] is True
    assert out["route"] == "freeroute"
    assert len(seen) == 1
    assert seen[0]["action"] == "leave"
    assert seen[0]["destination"] == "freeroute"


def test_research_egress_gates_omniroute_without_vendoring(monkeypatch):
    seen: list[dict] = []

    def fake_check(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "allowed": True}

    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        fake_check,
    )
    out = gate_research_egress(destination="http://127.0.0.1:20128")
    assert out["allowed"] is False
    assert out["route"] == "omniroute_gated"
    assert "20128" in " ".join(out["reasons"])
    assert seen == []


def test_study_tree_read_keeps_distill_only(tmp_path: Path):
    (tmp_path / "README.md").write_text("study analog", encoding="utf-8")
    out = read_study_tree("myn8n", root=tmp_path)
    assert out["ok"] is True
    assert out["allowed"] is True
    assert out["engine_role"] == "distill_only"
    assert out["engine_role"] != "product_engine"
    assert "README.md" in out["samples"]


def test_study_tree_missing_dir_is_still_distill_only(tmp_path: Path):
    missing = tmp_path / "absent"
    out = read_study_tree("langchain", root=missing)
    assert out["ok"] is True
    assert out["engine_role"] == "distill_only"
    assert out["exists"] is False


def test_study_tree_refuses_product_engine_promotion(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "CortexOS.execution.rsf_boundary.get_option",
        lambda oid: {"id": oid, "engine_role": "product_engine"},
    )
    out = read_study_tree("myn8n", root=tmp_path)
    assert out["allowed"] is False
    assert out["engine_role"] != "product_engine"


def test_gencfsm_dag_is_not_a_study_tree():
    out = read_study_tree("gencfsm_dag")
    assert out["allowed"] is False
    assert out["engine_role"] != "product_engine"
    assert "not a distill study analog" in " ".join(out["reasons"])
