"""RSF-06 distill harness: refuse-paste + happy Netie-native path."""

from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.constructor_graph import ConstructorGraphError, compile_constructor_graph
from CortexOS.execution.distill_harness import (
    BANNED_CONSTRUCTOR_KINDS,
    LOOP_STEPS,
    SCALE_SURFACES,
    STATUS_ABSTAIN,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_REFUSE,
    run_distill,
)
from CortexOS.execution.distill_options import REQUIRED_OPTION_IDS
from CortexOS.execution.rsf_orchestrator import BAKEOFF_SURFACES

HARNESS = (
    Path(__file__).resolve().parents[1] / "CortexOS" / "execution" / "distill_harness.py"
)
CONSTRUCTOR = (
    Path(__file__).resolve().parents[1] / "CortexOS" / "constructor_graph.py"
)


def test_loop_and_surfaces_are_honest() -> None:
    assert LOOP_STEPS == ("improve", "scale_check", "improve")
    assert SCALE_SURFACES == (*BAKEOFF_SURFACES, "constructor")
    assert BANNED_CONSTRUCTOR_KINDS == frozenset({"n8n", *REQUIRED_OPTION_IDS})


def test_happy_myn8n_emits_native_learn_trace(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("analog workflow names only", encoding="utf-8")
    out_dir = tmp_path / "traces"
    out = run_distill(
        {
            "option_id": "myn8n",
            "observations": ["named workflow nodes", "edges between steps"],
        },
        study_root=tmp_path,
        out_dir=out_dir,
    )
    assert out["ok"] is True
    assert out["status"] == STATUS_COMPLETE
    assert out["product_engine"] == "cortex"
    assert out["shipped_engine"] is None
    assert out["engine_role"] == "distill_only"
    assert out["engine_role"] != "product_engine"
    assert out["candidates"]
    for cand in out["candidates"]:
        assert cand["netie_target"].startswith("CortexOS.")
        assert cand["evidence"]
        assert "langchain" not in cand["netie_target"]
        assert cand["constructor_kind"] not in BANNED_CONSTRUCTOR_KINDS
    trace = out["learn_trace"]
    assert trace["source"] == "distill_harness"
    assert trace["option_id"] == "myn8n"
    assert trace["product_engine"] == "cortex"
    assert trace["facts"]
    assert (out_dir / "myn8n_learn_trace.json").is_file()
    md = (out_dir / "myn8n_capture.md").read_text(encoding="utf-8")
    assert "| Fact | Evidence | Confidence | Promote |" in md
    assert out["study"]["exists"] is True
    assert "README.md" in out["study"]["samples"]
    assert [step["step"] for step in out["loop"][:2]] == ["improve", "scale_check"]


def test_happy_gencfsm_studies_in_repo_module_not_a_third_orchestrator() -> None:
    out = run_distill({"option_id": "gencfsm_dag"})
    assert out["status"] == STATUS_COMPLETE
    assert out["engine_role"] == "learn"
    assert out["product_engine"] == "cortex"
    cand = out["candidates"][0]
    assert cand["netie_target"] == "CortexOS.execution.gen_cfsm"
    assert cand["via"] == "CortexOS.execution.dag_runner"
    samples = " ".join(out["study"]["samples"])
    assert "gen_cfsm.py" in samples
    assert "G1_GEN_CFSM_JEPA.md" in samples


def test_happy_langchain_maps_to_dag_runner_not_the_pypi_package(tmp_path: Path) -> None:
    out = run_distill(
        {
            "option_id": "langchain",
            "observations": ["chain then tool then parser"],
            "proposed": [
                {
                    "title": "Governed DAG instead of LC chain",
                    "netie_target": "CortexOS.execution.dag_runner",
                    "surfaces": ["dms_rag", "normal_chat", "agentic_actions"],
                    "evidence": ["chain then tool then parser"],
                    "egress": "freeroute",
                }
            ],
        },
        study_root=tmp_path / "missing",
        out_dir=tmp_path / "out",
    )
    assert out["status"] == STATUS_COMPLETE
    assert out["candidates"][0]["netie_target"] == "CortexOS.execution.dag_runner"
    assert "import langchain" not in (tmp_path / "out" / "langchain_capture.md").read_text(
        encoding="utf-8"
    )


def test_refuse_paste_product_engine() -> None:
    out = run_distill(
        {
            "option_id": "myn8n",
            "engine_role": "product_engine",
            "observations": ["paste n8n into Constructor"],
        }
    )
    assert out["ok"] is False
    assert out["status"] == STATUS_REFUSE
    assert out["candidates"] == []
    assert any("product_engine" in r for r in out["reasons"])
    assert out["product_engine"] == "cortex"


def test_refuse_paste_constructor_kind_langchain() -> None:
    out = run_distill(
        {
            "option_id": "langchain",
            "proposed": [
                {
                    "title": "ship LC as canvas kind",
                    "kind": "langchain",
                    "netie_target": "langchain.chains",
                    "evidence": ["docs.langchain.com"],
                }
            ],
        }
    )
    assert out["status"] == STATUS_REFUSE
    assert out["shipped_engine"] is None
    assert any("BAN" in r for r in out["reasons"])
    with pytest.raises(ConstructorGraphError, match="distill-only analog"):
        compile_constructor_graph(
            {"nodes": [{"id": "x", "kind": "langchain"}], "edges": []}
        )


def test_refuse_omniroute_vendor() -> None:
    out = run_distill(
        {
            "option_id": "langflow",
            "observations": ["flow canvas"],
            "destination": "http://127.0.0.1:20128",
        }
    )
    assert out["status"] == STATUS_REFUSE
    assert "20128" in " ".join(out["reasons"])


def test_non_native_target_does_not_complete() -> None:
    out = run_distill(
        {
            "option_id": "myn8n",
            "observations": ["nodes"],
            "proposed": [
                {
                    "title": "call analog SaaS",
                    "netie_target": "https://n8n.io/cloud",
                    "evidence": ["marketing page"],
                    "surfaces": ["constructor"],
                }
            ],
        }
    )
    assert out["ok"] is False
    assert out["status"] == STATUS_INCOMPLETE
    assert out["candidates"] == []
    assert any("invent-green" in r or "not a Netie-native" in r for r in out["reasons"])
    assert all(
        step.get("status") != STATUS_COMPLETE
        for step in out["loop"]
        if step["step"] == "improve"
    )


def test_retry_keeps_native_candidate_drops_bad(tmp_path: Path) -> None:
    (tmp_path / "flow.json").write_text("{}", encoding="utf-8")
    out = run_distill(
        {
            "option_id": "langflow",
            "observations": ["canvas nodes"],
            "proposed": [
                {
                    "title": "vendor analog runtime",
                    "netie_target": "https://langflow.example",
                    "evidence": ["upstream readme"],
                    "surfaces": ["constructor"],
                },
                {
                    "title": "Constructor compile path",
                    "netie_target": "CortexOS.constructor_graph",
                    "evidence": ["canvas nodes"],
                    "surfaces": ["constructor", "agentic_actions"],
                    "egress": "freeroute",
                },
            ],
        },
        study_root=tmp_path,
    )
    assert out["status"] == STATUS_COMPLETE
    assert [c["netie_target"] for c in out["candidates"]] == ["CortexOS.constructor_graph"]
    assert out["loop"][1]["ok"] is False
    assert out["loop"][-1]["ok"] is True


def test_unknown_option_abstains() -> None:
    out = run_distill({"option_id": "crewai"})
    assert out["status"] == STATUS_ABSTAIN
    assert out["ok"] is False
    assert out["candidates"] == []


def test_empty_recipe_abstains() -> None:
    out = run_distill({})
    assert out["status"] == STATUS_ABSTAIN
    assert "option_id required" in " ".join(out["reasons"])


def test_promoted_registry_role_is_refuse(monkeypatch) -> None:
    monkeypatch.setattr(
        "CortexOS.execution.distill_harness.get_option",
        lambda oid: {"id": oid, "engine_role": "product_engine", "blurb": "x"},
    )
    out = run_distill({"option_id": "myn8n", "observations": ["nodes"]})
    assert out["status"] == STATUS_REFUSE
    assert out["engine_role"] != "product_engine"
    assert out["candidates"] == []


def test_harness_source_stays_fail_closed_and_engine_clean() -> None:
    src = HARNESS.read_text(encoding="utf-8")
    lower = src.lower()
    assert "product_engine" in lower
    assert "run_distill" in src
    assert "scale_check" in src
    assert "import n8n" not in src
    assert "import langchain" not in src
    assert "import langflow" not in src
    assert "from langchain" not in src
    assert "from langflow" not in src
    assert "from n8n" not in src
    assert "urlopen" not in src
    assert "127.0.0.1:20128" not in src
    assert "do not vendor OmniRoute" in src
    ctor = CONSTRUCTOR.read_text(encoding="utf-8")
    assert "_BANNED_ENGINE_KINDS" in ctor
