"""CORTEX-COT-CLIMB: CoT/route/improve consumes FreeRoute. Does not rewrite it."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from CortexOS.crew import cot_climb, insights
from CortexOS.execution.gen_cfsm import DECISION_TERMINATE

COT_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "cot_climb.py"
ROOT = Path(__file__).resolve().parents[2]


def _ranking() -> dict[str, Any]:
    return insights.retrieve_ontology("how many skus")


def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "vault_live": True,
            "detail": "vault-armed",
            "live_5000_ci": False,
        },
    )


def _script(*texts: str, think: str = "Use inventory sku. No numbers.") -> Callable[..., Any]:
    leftover = list(texts)

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        assert purpose in {"think", "generative_ask"}
        if purpose == "think":
            return {
                "ok": True,
                "text": think,
                "identity": "cortex:crew:think",
                "route": {"label": "deepseek", "model": "deepseek-chat"},
            }
        body = leftover.pop(0) if leftover else "SELECT secret FROM payroll"
        return {
            "ok": True,
            "text": body,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
        }

    return fake


def test_public_map_cites_baseline_and_is_not_complete() -> None:
    body = cot_climb.public_map()
    assert body["complete"] is False
    assert body["status"] == "INCOMPLETE"
    assert body["issue_211_complete"] is False
    assert body["issue_212_complete"] is False
    assert body["replaces_baseline"] is False
    assert body["invented_better"] is False
    assert body["live_5000_ci"] is False
    base = body["measured_baseline"]
    assert base["gen"] == "57.69%"
    assert base["exact"] == "38.46%"
    assert base["wrong"] == 0
    assert "d2f116a6" in base["cite"]
    assert "215" in body["insights_wire"]
    law = insights.public_law()
    assert law["cot_climb"]["complete"] is False
    assert law["cot_climb"]["status"] == "INCOMPLETE"
    assert law["cot_climb"]["issue_212_complete"] is False
    assert law["cot_climb"]["measured_baseline"]["gen"] == "57.69%"
    assert "cot_climb" in law["generate"]


def test_source_is_netie_native_not_framework_paste() -> None:
    src = COT_PY.read_text(encoding="utf-8")
    assert "import langgraph" not in src
    assert "from langgraph" not in src
    assert "import langchain" not in src
    assert "from langchain" not in src
    assert "LIVE_KEY" not in src
    assert "openpyxl" not in src.lower()
    assert "import packs" not in src
    assert "from packs" not in src


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _diff_names_vs_main() -> set[str]:
    """Tree diff vs origin/main. Shallow CI clones fetch the tip; no rewrite."""
    if _git("rev-parse", "--verify", "origin/main").returncode != 0:
        fetched = _git("fetch", "--depth=1", "origin", "main")
        if fetched.returncode != 0:
            pytest.skip(
                "origin/main missing and fetch failed; cannot prove dual-write absence"
            )
    diff = _git("diff", "--name-only", "origin/main")
    assert diff.returncode == 0, diff.stderr
    return {line.strip() for line in diff.stdout.splitlines() if line.strip()}


def test_branch_does_not_dual_write_freeroute_layer() -> None:
    banned = {
        "CortexOS/crew/freeroute.py",
        "CortexOS/crew/openvault.py",
        "CortexOS/crew/config.py",
        "CortexOS/crew/llm.py",
        "CortexOS/crew/mcp_client.py",
        "CortexOS/dms/answer_engine.py",
        "CortexOS/dms/l2_generation.py",
        "CortexOS/integrations/freeroute.py",
        "CortexOS/integrations/openvault_client.py",
        "packs/dms/generative/l2_adapter.py",
        "packs/dms/generative/sql_generator.py",
        "tests/test_crew/test_freeroute.py",
        "tests/test_crew/test_openvault.py",
        "tests/conftest.py",
        "tests/freeroute_fake.py",
        "tests/test_freeroute_core.py",
    }
    overlap = sorted(_diff_names_vs_main() & banned)
    assert overlap == [], f"dual-write of #215 FreeRoute files: {overlap}"
    # server.py may gain unrelated crew routes (liberty seek #223). The
    # FreeRoute spend handlers themselves must not be rewritten.
    if "CortexOS/crew/server.py" in _diff_names_vs_main():
        src = (ROOT / "CortexOS" / "crew" / "server.py").read_text(encoding="utf-8")
        assert "async def freeroute_status" in src
        assert "async def freeroute_complete" in src
        assert "CALLER_KEY_RULE" in src
        diff = _git("diff", "origin/main", "--", "CortexOS/crew/server.py")
        assert diff.returncode == 0, diff.stderr
        assert "-    async def freeroute_complete" not in diff.stdout
        assert "-    async def freeroute_status" not in diff.stdout


@pytest.mark.asyncio
async def test_unarmed_fail_closed_no_invented_cot(crew_env) -> None:
    calls: list[str] = []

    async def boom(*args, **kwargs):  # noqa: ANN001
        calls.append("called")
        raise AssertionError("complete must not run when unarmed")

    env = await cot_climb.climb("how many skus", _ranking(), complete=boom)
    assert env["status"] == "REFUSE"
    assert env["ok"] is False
    assert env["values"] == []
    assert env["sql"] is None
    assert env["valid"] is False
    assert env["complete"] is False
    reason = env["refuse_reason"].lower()
    assert "unarmed" in reason or "invent-green" in reason
    assert "999" not in env["note"]
    assert calls == []
    assert env["climb"]["final"] == "UNARMED"
    assert env["measured_baseline"]["gen"] == "57.69%"


@pytest.mark.asyncio
async def test_think_then_valid_sql_abstains_no_numbers(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    fake = _script(
        "there are 999 skus\n```sql\nSELECT COUNT(DISTINCT sku) AS sku_count FROM inventory\n```"
    )
    env = await cot_climb.climb("how many skus", _ranking(), complete=fake)
    assert env["status"] == "ABSTAIN"
    assert env["ok"] is True
    assert env["valid"] is True
    assert env["values"] == []
    assert "inventory" in (env["sql"] or "").lower()
    assert "999" not in env["note"]
    assert "999" not in str(env["values"])
    assert env["complete"] is False
    assert env["climb"]["final"] == DECISION_TERMINATE
    assert env["climb"]["g1"]["horizon"] == 3
    assert env["measured_baseline"]["exact"] == "38.46%"
    assert env["measured_baseline"]["gen"] == "57.69%"


@pytest.mark.asyncio
async def test_improve_retries_after_off_ontology_sql(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    fake = _script(
        "SELECT secret FROM payroll",
        "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
    )
    env = await cot_climb.climb("how many skus", _ranking(), complete=fake)
    assert env["status"] == "ABSTAIN"
    assert env["valid"] is True
    assert "inventory" in (env["sql"] or "").lower()
    assert "payroll" not in (env["sql"] or "").lower()
    kinds = [row["kind"] for row in env["climb"]["steps"]]
    assert kinds.count("generate") == 2
    assert env["values"] == []
    assert env["complete"] is False


@pytest.mark.asyncio
async def test_horizon_exhaust_refuses_without_invented_sql(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    fake = _script(
        "SELECT secret FROM payroll",
        "DROP TABLE inventory",
        "SELECT * FROM secrets",
    )
    env = await cot_climb.climb("how many skus", _ranking(), complete=fake)
    assert env["status"] == "REFUSE"
    assert env["ok"] is False
    assert env["sql"] is None
    assert env["valid"] is False
    assert env["values"] == []
    assert env["complete"] is False
    assert "not COMPLETE" in env["refuse_reason"] or "horizon" in env["refuse_reason"].lower()
    assert env["climb"]["final"]


@pytest.mark.asyncio
async def test_think_refuse_fail_closed(crew_env, monkeypatch: pytest.MonkeyPatch) -> None:
    _armed(monkeypatch)

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, prompt, kwargs
        return {
            "ok": False,
            "refused": "OpenVault FreeRoute unarmed: vault sealed",
            "identity": "cortex:crew:think",
        }

    env = await cot_climb.climb("how many skus", _ranking(), complete=fake)
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert env["sql"] is None
    assert env["climb"]["final"] == "THINK_REFUSE"


@pytest.mark.asyncio
async def test_measure_reports_coverage_vs_baseline_not_a_better_percent(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    ranking = _ranking()
    cases = [
        {
            "id": "valid",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script(
                "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
            ),
        },
        {
            "id": "improve",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script(
                "SELECT secret FROM payroll",
                "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
            ),
        },
        {
            "id": "never",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script("SELECT secret FROM payroll", "SELECT x FROM payroll", "SELECT y FROM payroll"),
        },
        {
            "id": "empty",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script("", "", ""),
        },
        {
            "id": "ddl",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script("DROP TABLE inventory", "DELETE FROM inventory", "ALTER TABLE inventory"),
        },
    ]
    report = await cot_climb.measure_climb(cases)
    assert report["complete"] is False
    assert report["status"] == "INCOMPLETE"
    assert report["replaces_baseline"] is False
    assert report["invented_better"] is False
    assert report["like_with_like"] is False
    assert report["measured_baseline"]["gen"] == "57.69%"
    assert report["measured_baseline"]["exact"] == "38.46%"
    assert report["measured_baseline"]["wrong"] == 0
    assert report["this_run"]["n"] == 5
    assert report["this_run"]["validated"] == 2
    assert report["this_run"]["wrong"] == 0
    assert report["this_run"]["gen"] == "40.00%"
    assert report["vs_baseline"]["baseline_gen"] == "57.69%"
    assert report["vs_baseline"]["this_run_gen"] == "40.00%"
    assert report["this_run"]["gen"] != "57.69%"
    assert "99.95" not in str(report)
    assert report["issue_211_complete"] is False
    assert report["issue_212_complete"] is False


@pytest.mark.asyncio
async def test_insights_generate_exposes_climb_on_the_envelope(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)

    class _Bridge:
        async def ask(self, question: str) -> dict[str, Any]:
            raise AssertionError(f"ask must not run: {question}")

    fake = _script("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")
    env = await insights.run_insights(
        "how many skus",
        bridge=_Bridge(),  # type: ignore[arg-type]
        ask=False,
        generate=True,
        complete=fake,
    )
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    climb = (env.get("generative") or {}).get("climb") or {}
    assert climb.get("final") == DECISION_TERMINATE
    assert climb.get("complete") is False
    text = insights.render_tool_text(env)
    assert "climb:" in text
    assert "complete=False" in text
    assert "999" not in text
    assert climb.get("measured_baseline", {}).get("gen") == "57.69%"
    assert env["generative"]["validator"].startswith("static sqlglot guardrail")

