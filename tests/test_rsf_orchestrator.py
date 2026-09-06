"""RSF-04 orchestrator: honest CERTIFIED|ABSTAIN|REFUSE; no invent-green downstream."""

from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.execution.distill_options import option_ids
from CortexOS.execution.rsf_orchestrator import (
    BAKEOFF_SURFACES,
    PIPELINE_STAGES,
    bakeoff_hooks,
    run_rsf,
)
from CortexOS.rsf import RSF_STAGES, RsfConsumerError, parse_rsf_artifact, parse_rsf_trace

ORCH = (
    Path(__file__).resolve().parents[1]
    / "CortexOS"
    / "execution"
    / "rsf_orchestrator.py"
)


def _allow_leave(monkeypatch) -> None:
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        lambda **kwargs: {"ok": True, "allowed": True},
    )


def _proposal(chosen: str, options: list[str], evidence: list[str]) -> dict:
    return {
        "options": options,
        "chosen_option": chosen,
        "evidence": evidence,
        "reasons": ["test proposal"],
        "route_trace": [
            {
                "step": "pick",
                "considered": list(options),
                "chosen": chosen,
                "rejected": {},
                "note": "test",
            }
        ],
    }


def _happy_proposals() -> dict[str, dict]:
    return {
        "research": _proposal(
            "warehouse_grant",
            ["warehouse_grant", "web_search"],
            ["grant=sales_fact"],
        ),
        "segment": _proposal("region", ["region", "country"], ["column=sales_fact.region"]),
        "classify": _proposal("sku", ["sku", "customer"], ["metric=units_sold"]),
        "filter": _proposal("BETA", ["BETA", "ALPHA"], ["value-norm=SKU-BETA"]),
    }


def test_pipeline_order_matches_rsf02_schema() -> None:
    assert PIPELINE_STAGES == RSF_STAGES
    assert PIPELINE_STAGES == ("research", "segment", "classify", "filter")


def test_happy_path_emits_four_certified_artifacts(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    out = run_rsf("units sold by region for BETA", proposals=_happy_proposals())
    assert out["ok"] is True
    assert out["engine"] == "cortex"
    assert out["stages"] == list(RSF_STAGES)
    parsed = parse_rsf_trace(out["artifacts"])
    assert [a.stage for a in parsed] == list(RSF_STAGES)
    assert [a.status for a in parsed] == ["CERTIFIED"] * 4
    by_stage = {a["stage"]: a for a in out["artifacts"]}
    assert by_stage["segment"]["chosen_option"] == "region"
    assert by_stage["classify"]["chosen_option"] == "sku"
    assert by_stage["filter"]["chosen_option"] == "BETA"
    for art in out["artifacts"]:
        assert art["artifact_id"]
        assert art["question"]
        assert art["route_trace"]
        assert art["evidence"]
        assert art["chosen_option"] in art["options"]
        meta = art["route_trace"][-1]
        assert meta["step"] == "meta_router"
        assert meta["chosen"] == "cortex"
        assert "myn8n" in meta["rejected"]


def test_abstain_blocks_downstream_certified(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    proposals = _happy_proposals()
    proposals["segment"] = {
        "options": ["region", "country"],
        "chosen_option": None,
        "evidence": [],
        "reasons": ["grant does not name a segment key"],
    }
    out = run_rsf("units sold by region", proposals=proposals)
    assert out["ok"] is False
    by_stage = {a["stage"]: a for a in out["artifacts"]}
    assert by_stage["research"]["status"] == "CERTIFIED"
    assert by_stage["segment"]["status"] == "ABSTAIN"
    assert by_stage["segment"]["chosen_option"] is None
    assert by_stage["classify"]["status"] == "ABSTAIN"
    assert by_stage["classify"]["chosen_option"] is None
    assert by_stage["filter"]["status"] == "ABSTAIN"
    assert by_stage["filter"]["chosen_option"] is None
    assert "will not invent" in " ".join(by_stage["classify"]["reasons"])
    parse_rsf_trace(out["artifacts"])


def test_propose_is_not_called_after_abstain(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    seen: list[str] = []

    def propose(stage: str, question: str, priors):
        seen.append(stage)
        if stage == "research":
            return _proposal("warehouse_grant", ["warehouse_grant"], ["grant=sales_fact"])
        if stage == "segment":
            return {
                "options": ["region"],
                "chosen_option": None,
                "evidence": [],
                "reasons": ["not enough columns"],
            }
        raise AssertionError(f"propose must not run as CERTIFIED after abstain: {stage}")

    out = run_rsf("units sold", propose=propose)
    assert seen == ["research", "segment"]
    assert [a["status"] for a in out["artifacts"]] == [
        "CERTIFIED",
        "ABSTAIN",
        "ABSTAIN",
        "ABSTAIN",
    ]


def test_invent_green_proposal_becomes_abstain_not_certified(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    proposals = _happy_proposals()
    proposals["classify"] = {
        "options": ["sku", "customer"],
        "chosen_option": None,
        "evidence": ["looks fine"],
        "reasons": ["model guessed CERTIFIED"],
    }
    out = run_rsf("classify the segments", proposals=proposals)
    by_stage = {a["stage"]: a for a in out["artifacts"]}
    assert by_stage["classify"]["status"] == "ABSTAIN"
    assert by_stage["classify"]["chosen_option"] is None
    assert by_stage["filter"]["status"] == "ABSTAIN"
    with pytest.raises(RsfConsumerError):
        parse_rsf_artifact(
            {
                "stage": "classify",
                "status": "CERTIFIED",
                "chosen_option": None,
                "options": ["sku"],
                "evidence": ["looks fine"],
            }
        )


def test_research_gate_refuse_blocks_downstream(monkeypatch) -> None:
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        lambda **kwargs: {
            "ok": False,
            "allowed": False,
            "reasons": ["OpenVault unreachable: timeout"],
        },
    )
    out = run_rsf("research the field", proposals=_happy_proposals())
    assert out["ok"] is False
    by_stage = {a["stage"]: a for a in out["artifacts"]}
    assert by_stage["research"]["status"] == "REFUSE"
    assert by_stage["research"]["chosen_option"] is None
    assert any("unreachable" in r.lower() for r in by_stage["research"]["reasons"])
    for stage in ("segment", "classify", "filter"):
        assert by_stage[stage]["status"] == "ABSTAIN"
        assert by_stage[stage]["chosen_option"] is None
    parse_rsf_trace(out["artifacts"])


def test_banned_engine_choice_is_refuse_not_certified(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    proposals = _happy_proposals()
    proposals["research"] = _proposal(
        "langchain",
        ["cortex", "langchain", "myn8n"],
        ["docs.langchain.com"],
    )
    out = run_rsf("which orchestrator", proposals=proposals)
    by_stage = {a["stage"]: a for a in out["artifacts"]}
    assert by_stage["research"]["status"] == "REFUSE"
    assert by_stage["research"]["chosen_option"] is None
    assert "BAN" in " ".join(by_stage["research"]["reasons"])
    assert by_stage["segment"]["status"] == "ABSTAIN"


def test_gencfsm_dag_is_learn_not_a_third_orchestrator(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    proposals = _happy_proposals()
    proposals["research"] = _proposal(
        "gencfsm_dag",
        ["cortex", "gencfsm_dag", "myn8n"],
        ["CortexOS.execution.gen_cfsm"],
    )
    out = run_rsf("compile a dag", proposals=proposals)
    research = next(a for a in out["artifacts"] if a["stage"] == "research")
    assert research["status"] == "CERTIFIED"
    assert research["chosen_option"] == "gencfsm_dag"
    meta = research["route_trace"][-1]
    assert meta["chosen"] == "gencfsm_dag"
    assert "third orchestrator" in meta["note"].lower()
    assert out["engine"] == "cortex"


def test_options_registry_is_advisory_not_an_engine_swap(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    out = run_rsf("units sold", proposals=_happy_proposals())
    ids = option_ids()
    assert {"myn8n", "langchain", "langflow", "gencfsm_dag"} <= ids
    for art in out["artifacts"]:
        meta = art["route_trace"][-1]
        for oid in ("myn8n", "langchain", "langflow"):
            assert oid in meta["considered"]
            assert oid in meta["rejected"]
        assert meta["chosen"] != "langchain"
        assert meta["chosen"] != "myn8n"
        assert meta["chosen"] != "langflow"
    assert out["engine"] == "cortex"


def test_bakeoff_hooks_are_null_not_invented_targets() -> None:
    hooks = bakeoff_hooks()
    assert [h["surface"] for h in hooks] == list(BAKEOFF_SURFACES)
    for hook in hooks:
        for side in ("cortex_meta_route", "direct_option_adapter"):
            assert hook[side]["latency_ms"] is None
            assert hook[side]["cost_myr"] is None
            assert hook[side]["tokens"] is None
        assert hook["direct_option_adapter"]["option_ids"] == [
            "myn8n",
            "langchain",
            "langflow",
        ]
        assert "not executed" in hook["direct_option_adapter"]["note"]


def test_run_records_measured_latency_and_null_cost_tokens(monkeypatch) -> None:
    _allow_leave(monkeypatch)
    out = run_rsf("units sold", proposals=_happy_proposals())
    assert isinstance(out["latency_ms"], float)
    assert out["latency_ms"] >= 0.0
    assert out["cost_myr"] is None
    assert out["tokens"] is None
    assert out["bakeoff"] == bakeoff_hooks()


def test_empty_question_abstains_all_stages() -> None:
    out = run_rsf("   ")
    assert out["ok"] is False
    assert [a["status"] for a in out["artifacts"]] == ["ABSTAIN"] * 4
    for art in out["artifacts"]:
        assert art["chosen_option"] is None


def test_orchestrator_source_stays_fail_closed_and_engine_clean() -> None:
    src = ORCH.read_text(encoding="utf-8")
    lower = src.lower()
    assert "gate_research_egress" in src
    assert "parse_rsf_artifact" in src
    assert "parse_rsf_trace" in src
    assert "urlopen" not in src
    assert "127.0.0.1:20128" not in src
    assert "product_engine" in lower
    assert "import n8n" not in src
    assert "import langchain" not in src
    assert "import langflow" not in src
    assert "from langchain" not in src
    assert "from langflow" not in src
    assert "from n8n" not in src
