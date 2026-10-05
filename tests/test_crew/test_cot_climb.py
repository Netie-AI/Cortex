"""CORTEX-COT-CLIMB: CoT/route/improve consumes FreeRoute. Does not rewrite it."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from CortexOS.crew import cot_climb, insights
from CortexOS.execution.gen_cfsm import DECISION_TERMINATE

COT_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "cot_climb.py"
ROOT = Path(__file__).resolve().parents[2]
_C_STAMP_289_BASE = "5a5d728c4d6e376ace4a8b1af17f1a15f45b6af1"
_C_STAMP_289_BRANCH_EXCEPTIONS = {
    "cursor/c-stamp-289-bc64": frozenset(
        {
            "CortexOS/dms/answer_engine.py",
            "CortexOS/dms/l2_generation.py",
        }
    )
}
_C_STAMP_289_EXACT_DIFF = {
    "CortexOS/dms/answer_engine.py": (
        "73ef4932b5634681214769c8a798dd2849835b39337d44fbeee27f02a2fcc5c5",
        ("stamp_l2_route", "def _stamp_l2(", "stamp_l2_envelope", "require_route_stamp"),
    ),
    "CortexOS/dms/l2_generation.py": (
        "b9f1fb9fdb51c4d36aab1116921d27861cddc27d3f32ec4c74b4f073cac47e4e",
        (
            "L2_ROUTE_STAMP_MISSING_PREFIX",
            "require_route_stamp",
            "route_stamp",
            "def _missing_served_fields(",
            "def stamp_l2_envelope(",
        ),
    ),
}
# #288 Epic contingent ruling (2026-10-05): exact C7-04 subject-guard seam.
_C7_04_288_BASE = "c2b599f5bef7cc82e9b3f1f7f1e6117adf2d5ef5"
_C7_04_288_BRANCH_EXCEPTIONS = {
    "cursor/c7-04-288-subject-guard-c3fd": frozenset(
        {
            "CortexOS/dms/answer_engine.py",
            "tests/dms/test_c7_02_manifest_before_explain.py",
            "tests/dms/test_c7_05_crew_l2_serve.py",
        }
    )
}
_C7_04_288_EXACT_DIFF = {
    "CortexOS/dms/answer_engine.py": (
        "c056338f16d35bb344054929be86123ef8b0ebf6bc866144ae4f8fbe16b462cd",
        ("_metric_subject_mismatch", "does not match", "def _metric_subject_mismatch("),
    ),
    "tests/dms/test_c7_02_manifest_before_explain.py": (
        "77a51b62cca39985a8cfdb4b57ff6a665435d231513d4f701a97aea3d79e4d76",
        (
            "test_contract_ask_c7_04_workday_stockouts_abstains_by_name",
            "ma_workday_payroll_cube",
        ),
    ),
    "tests/dms/test_c7_05_crew_l2_serve.py": (
        "cc5f23b660c7dfa6af25d5ecf64f0792be92278c5f79a3b95f86fe2bb7b39e1c",
        ("test_c7_04_subject_misroute_now_abstains", "subject 'overtime'"),
    ),
}
# #254 Epic ruling (2026-10-05, 5999107355): exact runtime-log isolation seam.
_H2_254_BASE = "8c603ae80352f7084ab4ed33ff0bbc4620e67dd2"
_H2_254_BRANCH_EXCEPTIONS = {
    "cursor/h2-test-isolation-254-45db": frozenset({"tests/conftest.py"})
}
_H2_254_EXACT_DIFF = {
    "tests/conftest.py": (
        "c2a2ca0174f5655f78be834d5d450ed331a6363b323e9d7da23867fb7deadfd7",
        (
            "CORTEX_DECISION_LOG_PATH",
            "CORTEX_KEV_SHADOW_PATH",
            "tier_decisions.jsonl",
            "tier_shadow.jsonl",
            "@pytest.fixture(autouse=True)",
            "def isolate_runtime_logs(",
        ),
    )
}
# #277 Epic ruling (2026-10-05, c5994673962): one-file exception, same shape as
# #289's. The extractor pin may allow sqlglot for sql_extract.py only; any other
# edit to tests/test_freeroute_core.py needs its own exception.
_EXTRACT_PIN_277_BASE = "44efa32d99125ab0c7c72474cf27faa5b5e7ae68"
_EXTRACT_PIN_277_BRANCH_EXCEPTIONS = {
    "claude/dms-agi-db-accuracy-rw5en9": frozenset({"tests/test_freeroute_core.py"})
}
_EXTRACT_PIN_277_EXACT_DIFF = {
    "tests/test_freeroute_core.py": (
        "0589a84604b5221ce0c564b57e989071f7f3aa4f8aa582b565599d07f1f71679",
        ("_EXTRACT_EXTRA", '"CortexOS/integrations/freeroute.py") == []'),
    ),
}


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
    assert body["like_with_like"] is False
    assert body["like_with_like_corpus"] == cot_climb.LIKE_WITH_LIKE_CORPUS
    assert body["issue_227_complete"] is False
    assert "INCOMPLETE" in body["leftover"]
    assert "covering" in body["leftover"]
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


def _branch_name() -> str:
    from_ci = os.environ.get("GITHUB_HEAD_REF", "").strip()
    if from_ci:
        return from_ci
    result = _git("branch", "--show-current")
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_exact_c_stamp_289_seam(path: str) -> bool:
    """Allow only #289's frozen stamp diff; #290/#291 need new exceptions."""
    allowed = _C_STAMP_289_BRANCH_EXCEPTIONS.get(_branch_name(), frozenset())
    if path not in allowed:
        return False
    expected = _C_STAMP_289_EXACT_DIFF.get(path)
    if expected is None:
        return False
    diff = _git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        f"{_C_STAMP_289_BASE}..HEAD",
        "--",
        path,
    )
    assert diff.returncode == 0, diff.stderr
    expected_digest, seam_symbols = expected
    for symbol in seam_symbols:
        assert symbol in diff.stdout, f"#289 stamp seam missing {symbol!r} in {path}"
    stable_diff = "\n".join(
        line for line in diff.stdout.splitlines() if not line.startswith("index ")
    )
    digest = hashlib.sha256(f"{stable_diff}\n".encode()).hexdigest()
    assert digest == expected_digest, (
        f"#289 exception covers only its exact contract-ask stamp seam in {path}; "
        "#290/#291 or any other edit needs its own exception"
    )
    return True


def _is_exact_c7_04_288_seam(path: str) -> bool:
    """Allow only #288's frozen C7-04 diff on its licensed branch."""
    allowed = _C7_04_288_BRANCH_EXCEPTIONS.get(_branch_name(), frozenset())
    if path not in allowed:
        return False
    expected = _C7_04_288_EXACT_DIFF.get(path)
    if expected is None:
        return False
    if _git("cat-file", "-e", f"{_C7_04_288_BASE}^{{commit}}").returncode != 0:
        _git("fetch", "--depth=1", "origin", _C7_04_288_BASE)
    diff = _git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        f"{_C7_04_288_BASE}..HEAD",
        "--",
        path,
    )
    assert diff.returncode == 0, diff.stderr
    expected_digest, seam_symbols = expected
    for symbol in seam_symbols:
        assert symbol in diff.stdout, f"#288 C7-04 seam missing {symbol!r} in {path}"
    stable_diff = "\n".join(
        line for line in diff.stdout.splitlines() if not line.startswith("index ")
    )
    digest = hashlib.sha256(f"{stable_diff}\n".encode()).hexdigest()
    assert digest == expected_digest, (
        f"#288 exception covers only its exact C7-04 subject seam in {path}; "
        "any other edit needs its own exception"
    )
    return True


def _is_exact_h2_254_seam(path: str) -> bool:
    """Allow only #254's frozen test-log isolation diff on its licensed branch."""
    allowed = _H2_254_BRANCH_EXCEPTIONS.get(_branch_name(), frozenset())
    if path not in allowed:
        return False
    expected = _H2_254_EXACT_DIFF.get(path)
    if expected is None:
        return False
    if _git("cat-file", "-e", f"{_H2_254_BASE}^{{commit}}").returncode != 0:
        _git("fetch", "--depth=1", "origin", _H2_254_BASE)
    diff = _git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        f"{_H2_254_BASE}..HEAD",
        "--",
        path,
    )
    assert diff.returncode == 0, diff.stderr
    expected_digest, seam_symbols = expected
    for symbol in seam_symbols:
        assert symbol in diff.stdout, f"#254 isolation seam missing {symbol!r} in {path}"
    stable_diff = "\n".join(
        line for line in diff.stdout.splitlines() if not line.startswith("index ")
    )
    digest = hashlib.sha256(f"{stable_diff}\n".encode()).hexdigest()
    assert digest == expected_digest, (
        f"#254 exception covers only its exact runtime-log isolation seam in {path}; "
        "any other edit needs its own exception"
    )
    return True


def _is_exact_extract_pin_277(path: str) -> bool:
    """Allow only #277's frozen sqlglot allowance in the FreeRoute-core pin."""
    if path not in _EXTRACT_PIN_277_BRANCH_EXCEPTIONS.get(_branch_name(), frozenset()):
        return False
    expected_digest, symbols = _EXTRACT_PIN_277_EXACT_DIFF[path]
    if _git("cat-file", "-e", f"{_EXTRACT_PIN_277_BASE}^{{commit}}").returncode != 0:
        _git("fetch", "--depth=1", "origin", _EXTRACT_PIN_277_BASE)  # shallow CI clone
    diff = _git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        f"{_EXTRACT_PIN_277_BASE}..HEAD",
        "--",
        path,
    )
    assert diff.returncode == 0, diff.stderr
    for symbol in symbols:
        assert symbol in diff.stdout, f"#277 pin exception missing {symbol!r} in {path}"
    stable_diff = "\n".join(
        line for line in diff.stdout.splitlines() if not line.startswith("index ")
    )
    digest = hashlib.sha256(f"{stable_diff}\n".encode()).hexdigest()
    assert digest == expected_digest, (
        f"#277 exception covers only the sqlglot allowance for sql_extract.py in {path}; "
        "any other edit needs its own exception"
    )
    return True


def _held_freeroute_paths(names: set[str]) -> list[str]:
    return sorted(
        path
        for path in names
        if not (
            _is_exact_c_stamp_289_seam(path)
            or _is_exact_c7_04_288_seam(path)
            or _is_exact_h2_254_seam(path)
            or _is_exact_extract_pin_277(path)
        )
    )


def test_branch_does_not_dual_write_freeroute_layer() -> None:
    banned = {
        "CortexOS/crew/freeroute.py",
        "CortexOS/crew/openvault.py",
        "CortexOS/crew/config.py",
        "CortexOS/crew/llm.py",
        "CortexOS/crew/mcp_client.py",
        "CortexOS/dms/answer_engine.py",
        "CortexOS/dms/l2_generation.py",
        # #269 ROUTER-1 owns store schema / _write_row / _stats / pick in
        # CortexOS/integrations/freeroute.py. Arming + RouteStamp served_*
        # stay #272. Do not put crew/freeroute.py back on this allow.
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
    held = _held_freeroute_paths(set(overlap))
    assert held == [], f"dual-write of #215 FreeRoute files: {held}"
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


def test_other_branch_editing_answer_engine_still_trips_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "cursor/memory-290-not-c-stamp")
    path = "CortexOS/dms/answer_engine.py"
    assert _held_freeroute_paths({path}) == [path]


def test_other_branch_editing_conftest_still_trips_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "cursor/h2-test-isolation-254-unlicensed")
    path = "tests/conftest.py"
    assert _held_freeroute_paths({path}) == [path]


def test_other_branch_editing_freeroute_core_pin_still_trips_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "cursor/284-freeroute-cte-scope")
    path = "tests/test_freeroute_core.py"
    assert _held_freeroute_paths({path}) == [path]
    # #277's exception does not stretch to any other banned file.
    monkeypatch.setenv("GITHUB_HEAD_REF", "claude/dms-agi-db-accuracy-rw5en9")
    assert _held_freeroute_paths({"CortexOS/crew/freeroute.py"}) == [
        "CortexOS/crew/freeroute.py"
    ]


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
    assert env["climb"]["think_consumed"] is True
    assert env["climb"]["g1_consumed"] is True
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
    assert kinds.count("think") >= 2
    assert env["climb"]["prior_sql_consumed"] is True
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
    assert report["this_run"]["exact"] == "0.00%"
    assert report["this_run"]["gold_n"] == 0
    assert report["this_run"]["exact_matched"] == 0
    assert report["vs_baseline"]["baseline_gen"] == "57.69%"
    assert report["vs_baseline"]["this_run_gen"] == "40.00%"
    assert report["this_run"]["gen"] != "57.69%"
    assert "99.95" not in str(report)
    assert report["issue_211_complete"] is False
    assert report["issue_212_complete"] is False
    labeled = await cot_climb.measure_climb(cases, corpus=cot_climb.LIKE_WITH_LIKE_CORPUS)
    assert labeled["like_with_like"] is False
    assert labeled["this_run"]["n"] == 5
    assert labeled["complete"] is False


@pytest.mark.asyncio
async def test_think_text_is_consumed_in_sql_prompt(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    prompts: list[str] = []
    think = "PLAN: count distinct sku on inventory. No numbers."

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        if purpose == "think":
            return {
                "ok": True,
                "text": think,
                "identity": "cortex:crew:think",
                "route": {"label": "deepseek", "model": "deepseek-chat"},
            }
        prompts.append(prompt)
        return {
            "ok": True,
            "text": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
        }

    env = await cot_climb.climb(
        "how many skus",
        _ranking(),
        complete=fake,
        ideas=["Learn via existing gen_cfsm compile into dag_runner", "import langgraph"],
    )
    assert env["status"] == "ABSTAIN"
    assert env["climb"]["think_consumed"] is True
    assert env["climb"]["g1_consumed"] is True
    assert prompts
    assert "THINK" in prompts[0]
    assert "count distinct sku" in prompts[0].lower()
    assert "G1 cFSM PLAN" in prompts[0]
    assert "step_1" in prompts[0]
    assert "gen_cfsm" in prompts[0]
    assert "langgraph" not in prompts[0].lower()


@pytest.mark.asyncio
async def test_pinned_26_is_like_with_like_without_inventing_percent(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    assert len(cot_climb.DMS_180_CURATED) == 26
    fake = _script("", "", "")
    report = await cot_climb.measure_climb(
        corpus=cot_climb.LIKE_WITH_LIKE_CORPUS,
        complete=fake,
    )
    assert report["this_run"]["n"] == 26
    assert report["like_with_like"] is True
    assert report["this_run"]["validated"] == 0
    assert report["this_run"]["wrong"] == 0
    assert report["this_run"]["gen"] == "0.00%"
    assert report["vs_baseline"]["improved"] is False
    assert report["vs_baseline"]["this_run_gen"] == "0.00%"
    assert report["vs_baseline"]["this_run_gen"] != "57.69%"
    assert report["this_run"]["exact"] == "0.00%"
    assert report["this_run"]["exact"] != "38.46%"
    assert report["vs_baseline"]["this_run_exact"] == "0.00%"
    assert report["this_run"]["exact_matched"] == 0
    assert report["this_run"]["gold_n"] >= 1
    assert report["complete"] is False
    assert report["status"] == "INCOMPLETE"
    assert report["replaces_baseline"] is False
    assert report["invented_better"] is False
    assert report["issue_212_complete"] is False
    ids = [str(row["id"]) for row in report["outcomes"]]
    assert set(ids) == set(cot_climb.DMS_180_IDS)


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
    assert climb.get("g1_consumed") is True


@pytest.mark.asyncio
async def test_g1_plan_is_consumed_in_think_prompt(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    think_prompts: list[str] = []

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        if purpose == "think":
            think_prompts.append(prompt)
            return {
                "ok": True,
                "text": "Use inventory sku. No numbers.",
                "identity": "cortex:crew:think",
                "route": {"label": "deepseek", "model": "deepseek-chat"},
            }
        return {
            "ok": True,
            "text": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
        }

    env = await cot_climb.climb("how many skus", _ranking(), complete=fake)
    assert env["status"] == "ABSTAIN"
    assert env["climb"]["g1_consumed"] is True
    assert think_prompts
    assert "G1 cFSM PLAN" in think_prompts[0]
    assert "step_1" in think_prompts[0]
    assert "langgraph" not in think_prompts[0].lower()
    assert env["complete"] is False
    assert env["climb"]["complete"] is False


@pytest.mark.asyncio
async def test_improve_think_consumes_prior_sql_and_route(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    think_prompts: list[str] = []
    leftover = [
        "SELECT secret FROM payroll",
        "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
    ]

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        if purpose == "think":
            think_prompts.append(prompt)
            return {
                "ok": True,
                "text": "Use inventory sku. No numbers.",
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

    env = await cot_climb.climb("how many skus", _ranking(), complete=fake)
    assert env["status"] == "ABSTAIN"
    assert env["climb"]["prior_sql_consumed"] is True
    assert len(think_prompts) >= 2
    assert "PRIOR SQL" in think_prompts[1]
    assert "payroll" in think_prompts[1].lower()
    assert "G1 route_step" in think_prompts[1]
    assert env["values"] == []
    assert env["complete"] is False


@pytest.mark.asyncio
async def test_exact_scores_certified_gold_without_replacing_baseline(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    ranking = _ranking()
    gold = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
    hit = await cot_climb.measure_climb(
        [
            {
                "id": "cq_sku_count",
                "intent": "How many SKUs do we have in inventory?",
                "ranking": ranking,
                "expected_sql": gold,
                "complete": _script(gold),
            }
        ]
    )
    assert hit["complete"] is False
    assert hit["status"] == "INCOMPLETE"
    assert hit["like_with_like"] is False
    assert hit["replaces_baseline"] is False
    assert hit["invented_better"] is False
    assert hit["issue_212_complete"] is False
    assert hit["this_run"]["n"] == 1
    assert hit["this_run"]["validated"] == 1
    assert hit["this_run"]["wrong"] == 0
    assert hit["this_run"]["gen"] == "100.00%"
    assert hit["this_run"]["exact"] == "100.00%"
    assert hit["this_run"]["exact_matched"] == 1
    assert hit["this_run"]["gold_n"] == 1
    assert hit["vs_baseline"]["this_run_exact"] != "38.46%"
    assert hit["vs_baseline"]["improved"] is False
    assert hit["this_run"]["exact"] != "38.46%"
    assert "99.95" not in str(hit)

    miss = await cot_climb.measure_climb(
        [
            {
                "id": "cq_sku_count",
                "intent": "How many SKUs do we have in inventory?",
                "ranking": ranking,
                "expected_sql": gold,
                "complete": _script("SELECT sku FROM inventory"),
            }
        ]
    )
    assert miss["this_run"]["validated"] == 1
    assert miss["this_run"]["gen"] == "100.00%"
    assert miss["this_run"]["exact"] == "0.00%"
    assert miss["this_run"]["exact_matched"] == 0
    assert miss["like_with_like"] is False
    assert miss["vs_baseline"]["improved"] is False
    assert miss["complete"] is False
    assert miss["this_run"]["exact"] != "38.46%"


def test_public_map_leftover_stays_incomplete_covering() -> None:
    body = cot_climb.public_map()
    assert "INCOMPLETE" in body["leftover"]
    assert "exact" in body["leftover"].lower() or "G1" in body["leftover"]
    assert body["complete"] is False
    assert body["issue_212_complete"] is False
    gold = cot_climb.certified_gold_sql()
    assert "cq_sku_count" in gold
    assert cot_climb.sql_exact("", gold["cq_sku_count"]) is False
    assert cot_climb.sql_exact(gold["cq_sku_count"], gold["cq_sku_count"]) is True


