"""RSF-07 eval corpus + Audit/Operate trace. Precision-on-answered; no invent-green."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from CortexOS.execution.rsf_eval import (
    ARTIFACTS_DIR,
    CASES_PATH,
    load_artifact_run,
    load_cases,
    load_poison_run,
    run_eval,
    score_trace,
    threshold_violations,
)
from CortexOS.execution.rsf_operate import render_audit_operate
from CortexOS.execution.rsf_orchestrator import BAKEOFF_SURFACES
from CortexOS.rsf import RSF_STAGES, RsfConsumerError, parse_rsf_trace

ROOT = Path(__file__).resolve().parents[1]
EVAL_PY = ROOT / "CortexOS" / "execution" / "rsf_eval.py"
OPERATE_PY = ROOT / "CortexOS" / "execution" / "rsf_operate.py"
SKIN_APP = ROOT / "CortexOS" / "constructor_skin" / "app.js"
SKIN_HTML = ROOT / "CortexOS" / "constructor_skin" / "index.html"


@pytest.fixture
def allow_leave(monkeypatch) -> None:
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_gate.check_gate",
        lambda **kwargs: {"ok": True, "allowed": True},
    )


def test_corpus_covers_happy_abstain_refuse_and_surfaces() -> None:
    cases = load_cases()
    ids = {c["id"] for c in cases}
    paths = {c["path"] for c in cases}
    surfaces = {c["surface"] for c in cases}
    assert "happy_dms_rag" in ids
    assert "abstain_segment" in ids
    assert "refuse_langchain" in ids
    assert "refuse_omniroute" in ids
    assert {"happy", "abstain", "refuse"} <= paths
    assert {"dms_rag", "normal_chat", "agentic_actions"} <= surfaces
    assert (ARTIFACTS_DIR / "happy_dms_rag.json").is_file()
    assert (ARTIFACTS_DIR / "abstain_segment.json").is_file()
    assert (ARTIFACTS_DIR / "refuse_langchain.json").is_file()
    assert CASES_PATH.is_file()


def test_live_eval_wrong_is_zero(allow_leave) -> None:
    report = run_eval()
    assert report["totals"]["wrong"] == 0, report
    assert report["totals"]["regression"] == 0, report
    assert report["pass"] is True
    assert not report["violations"]
    assert set(BAKEOFF_SURFACES) <= set(report["surfaces"])
    by_id = {row["id"]: row for row in report["results"]}
    assert by_id["happy_dms_rag"]["ok"] is True
    assert [s["pred_status"] for s in by_id["happy_dms_rag"]["stages"]] == ["CERTIFIED"] * 4
    assert [s["pred_status"] for s in by_id["abstain_segment"]["stages"]] == [
        "CERTIFIED",
        "ABSTAIN",
        "ABSTAIN",
        "ABSTAIN",
    ]
    assert [s["pred_status"] for s in by_id["refuse_langchain"]["stages"]] == [
        "REFUSE",
        "ABSTAIN",
        "ABSTAIN",
        "ABSTAIN",
    ]
    assert [s["pred_status"] for s in by_id["refuse_omniroute"]["stages"]] == [
        "REFUSE",
        "ABSTAIN",
        "ABSTAIN",
        "ABSTAIN",
    ]
    happy = by_id["happy_dms_rag"]["run"]
    assert happy["engine"] == "cortex"
    assert happy["cost_myr"] is None
    assert happy["tokens"] is None
    for hook in happy["bakeoff"]:
        assert hook["cortex_meta_route"]["latency_ms"] is None
        assert hook["direct_option_adapter"]["latency_ms"] is None
        assert "not executed" in hook["direct_option_adapter"]["note"]


def test_happy_agentic_certifies_gencfsm_learn_not_langchain(allow_leave) -> None:
    report = run_eval()
    row = next(r for r in report["results"] if r["id"] == "happy_agentic_gencfsm")
    research = row["stages"][0]
    assert research["pred_status"] == "CERTIFIED"
    assert research["pred_chosen"] == "gencfsm_dag"
    assert row["engine"] == "cortex"
    meta = row["run"]["artifacts"][0]["route_trace"][-1]
    assert meta["chosen"] == "gencfsm_dag"
    assert meta["chosen"] != "langchain"


def test_poison_invent_green_is_wrong_and_fails_thresholds() -> None:
    poison = load_poison_run()
    scored = score_trace(poison["artifacts"], poison["gold"])
    assert scored["wrong"] >= 1
    report = {
        "totals": {
            "total": 1,
            "correct": 0,
            "wrong": scored["wrong"],
            "abstain": 0,
            "error": 0,
            "regression": 0,
        },
        "regressions": [],
    }
    violations = threshold_violations(report)
    assert violations
    assert "confidently_wrong" in violations[0]
    with pytest.raises(RsfConsumerError, match="must not be CERTIFIED"):
        parse_rsf_trace(poison["artifacts"])


def test_wrong_chosen_option_is_confidently_wrong() -> None:
    gold = {
        "statuses": ["CERTIFIED", "CERTIFIED", "CERTIFIED", "CERTIFIED"],
        "chosen": {
            "research": "warehouse_grant",
            "segment": "region",
            "classify": "sku",
            "filter": "BETA",
        },
    }
    artifacts = load_artifact_run("happy_dms_rag")["artifacts"]
    mutated = json.loads(json.dumps(artifacts))
    mutated[3]["chosen_option"] = "ALPHA"
    scored = score_trace(mutated, gold)
    assert scored["wrong"] == 1
    assert scored["stages"][3]["outcome"] == "wrong"


def test_fixture_audit_operate_shows_statuses_without_fake_green() -> None:
    happy = render_audit_operate(load_artifact_run("happy_dms_rag"))
    assert happy["audit"]["ok"] is True
    assert [s["status"] for s in happy["audit"]["stages"]] == ["CERTIFIED"] * 4
    assert happy["operate"]["stages"][3]["chosen_option"] == "BETA"
    assert happy["operate"]["stages"][3]["route_trace"]
    assert happy["audit"]["invented_certified"] is False

    abstain = render_audit_operate(load_artifact_run("abstain_segment"))
    statuses = [s["status"] for s in abstain["audit"]["stages"]]
    assert statuses == ["CERTIFIED", "ABSTAIN", "ABSTAIN", "ABSTAIN"]
    assert abstain["audit"]["stages"][1]["chosen_option"] is None
    assert abstain["operate"]["ok"] is False
    assert all(s["displayed_certified"] is False for s in abstain["audit"]["stages"][1:])

    refuse = render_audit_operate(load_artifact_run("refuse_langchain"))
    assert [s["status"] for s in refuse["audit"]["stages"]] == [
        "REFUSE",
        "ABSTAIN",
        "ABSTAIN",
        "ABSTAIN",
    ]
    assert refuse["operate"]["stages"][0]["chosen_option"] is None
    assert any("BAN" in r for r in refuse["audit"]["stages"][0]["reasons"])


def test_audit_operate_strips_invented_certified_after_gap() -> None:
    poison = load_poison_run()
    views = render_audit_operate(poison)
    statuses = [s["status"] for s in views["audit"]["stages"]]
    assert statuses == ["CERTIFIED", "ABSTAIN", "ABSTAIN", "ABSTAIN"]
    classify = views["audit"]["stages"][2]
    assert classify["displayed_certified"] is False
    assert classify["chosen_option"] is None
    assert "will not display invented CERTIFIED" in " ".join(classify["reasons"])
    operate_classify = views["operate"]["stages"][2]
    assert operate_classify["status"] == "ABSTAIN"
    assert operate_classify["chosen_option"] is None
    assert views["audit"]["ok"] is False
    assert views["audit"]["legal"] is False


def test_live_eval_attaches_audit_operate(allow_leave) -> None:
    report = run_eval()
    row = next(r for r in report["results"] if r["id"] == "abstain_segment")
    assert [s["status"] for s in row["audit"]["stages"]] == [
        "CERTIFIED",
        "ABSTAIN",
        "ABSTAIN",
        "ABSTAIN",
    ]
    assert row["operate"]["stages"][0]["chosen_option"] == "warehouse_grant"
    assert row["operate"]["stages"][1]["chosen_option"] is None
    assert row["operate"]["bakeoff_surfaces"] == list(BAKEOFF_SURFACES)


def test_eval_and_operate_modules_stay_engine_clean() -> None:
    for path in (EVAL_PY, OPERATE_PY):
        src = path.read_text(encoding="utf-8")
        assert "import n8n" not in src
        assert "import langchain" not in src
        assert "import langflow" not in src
        assert "from langchain" not in src
        assert "from langflow" not in src
        assert "from n8n" not in src
        assert "urlopen" not in src
        assert "127.0.0.1:20128" not in src
        assert "will not invent" in src or "will not display invented" in src
    assert "run_rsf" in EVAL_PY.read_text(encoding="utf-8")
    assert list(RSF_STAGES) == ["research", "segment", "classify", "filter"]


def test_constructor_audit_operate_skin_does_not_default_certified() -> None:
    app = SKIN_APP.read_text(encoding="utf-8")
    html = SKIN_HTML.read_text(encoding="utf-8")
    assert 'id="rsf-audit"' in html
    assert ">OPERATE<" in html
    assert 'id="rsf-operate"' in html
    assert "function rsfStatusClass(status)" in app
    assert 'if (status === "CERTIFIED") return "rsf-certified"' in app
    assert 'return "rsf-abstain"' in app
    default_line = next(
        line.strip()
        for line in app.splitlines()
        if "return" in line and "rsf-" in line and "CERTIFIED" not in line
    )
    assert "rsf-certified" not in default_line
    assert 'status === "CERTIFIED" ? row.chosen_option' in app
