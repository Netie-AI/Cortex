"""EPIC-PROMPT-HARNESS-CLIMB: measure vs #180; refuse invent-COMPLETE / GPU / trained."""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import insights
from CortexOS.crew import prompt_harness_climb as harness
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM

HARNESS_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "prompt_harness_climb.py"
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
# #104 part 2 Lead climb GO (2026-10-05): exact ask-route L2 gate seam.
_C7_05_104P2_BASE = "8e0269e553bb83d246c6ab331f89d16a51033ade"
_C7_05_104P2_BRANCH_EXCEPTIONS = {
    "cursor/c7-05-104p2-l2-ab-a1f3": frozenset(
        {
            "CortexOS/dms/answer_engine.py",
        }
    )
}
_C7_05_104P2_EXACT_DIFF = {
    "CortexOS/dms/answer_engine.py": (
        "9acf58a418a7a33af0c95d4f51fa65ff9096794390032e841393602b159ee0c9",
        ("l2_plan_gates", "ask_ranking", "L2 plan gate", "L2 plan shape"),
    ),
}
# #105 C7-06 refresh of #128: exact L1 serve-chooser seam. Pending Lead CLEAR;
# the PR stays draft until the C7-06 held-out gate is met.
_C7_06_105_BASE = "c7469da4e87cb49f56930242189b10817cba7d04"
_C7_06_105_BRANCH_EXCEPTIONS = {
    "cursor/c7-06-refresh-c7469da-9556": frozenset({"CortexOS/dms/answer_engine.py"})
}
_C7_06_105_EXACT_DIFF = {
    "CortexOS/dms/answer_engine.py": (
        "07e79c66ef08c4091201cea8aef974bdcf4996f13bf8190d39cf4f81c6e4b806",
        ("choose_governed_metric", "cascade_retired"),
    ),
}


def _ranking() -> dict[str, Any]:
    return insights.retrieve_ontology("how many skus")


def _armed_free(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(
        fr,
        "candidates",
        lambda purpose="generative_ask": [
            {
                "label": "deepseek",
                "model": "deepseek-chat",
                "kind": "freeroute",
                "source": "test",
            }
        ],
    )


def _script(*texts: str, think: str = "Use inventory sku. No numbers.") -> Callable[..., Any]:
    leftover = list(texts)

    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        assert purpose in {"think", "generative_ask", "prompt"}
        if purpose in {"think", "prompt"}:
            return {
                "ok": True,
                "text": think,
                "identity": "cortex:crew:think",
                "route": {"label": "deepseek", "model": "deepseek-chat", "kind": "freeroute"},
            }
        body = leftover.pop(0) if leftover else "SELECT secret FROM payroll"
        return {
            "ok": True,
            "text": body,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat", "kind": "freeroute"},
            "model": "deepseek-chat",
            "stamp": {"requested": "deepseek-chat", "served": "deepseek-chat"},
        }

    return fake


def _cases(complete: Callable[..., Any]) -> list[dict[str, Any]]:
    ranking = _ranking()
    sql_ok = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
    return [
        {
            "id": "valid",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": complete,
        },
        {
            "id": "improve",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script("SELECT secret FROM payroll", sql_ok),
        },
        {
            "id": "never",
            "intent": "how many skus",
            "ranking": ranking,
            "complete": _script(
                "SELECT secret FROM payroll",
                "SELECT x FROM payroll",
                "SELECT y FROM payroll",
            ),
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
            "complete": _script(
                "DROP TABLE inventory",
                "DELETE FROM inventory",
                "ALTER TABLE inventory",
            ),
        },
    ]


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def test_public_map_cites_baseline_and_does_not_close_212() -> None:
    body = harness.public_map()
    assert body["complete"] is False
    assert body["status"] == "INCOMPLETE"
    assert body["issue"] == 227
    assert body["issue_212_complete"] is False
    assert body["closes_212"] is False
    assert body["like_with_like"] is False
    assert body["gpu_finetune"] is False
    assert body["distill"]["ideas_only"] is True
    assert body["live_5000_ci"] is False
    assert body["live_8020_ci"] is False
    base = body["measured_baseline"]
    assert base["gen"] == "57.69%"
    assert base["exact"] == "38.46%"
    assert base["wrong"] == 0
    assert "d2f116a6" in base["cite"]
    law = insights.public_law()
    assert law["prompt_harness"]["complete"] is False
    assert law["prompt_harness"]["status"] == "INCOMPLETE"
    assert law["prompt_harness"]["issue_212_complete"] is False
    assert law["prompt_harness"]["closes_212"] is False
    assert law["cot_climb"]["status"] == "INCOMPLETE"
    assert law["cot_climb"]["issue_212_complete"] is False


def test_model_lane_free_normal_not_premium() -> None:
    assert harness.model_lane("deepseek-chat") == "normal"
    assert harness.model_lane("qwen/qwen3.6-27b") == "normal"
    assert harness.model_lane("meta-llama/llama-3.3-70b-instruct:free") == "free"
    assert harness.model_lane("openai/gpt-oss-20b") == "normal"
    assert harness.model_lane("gpt-4o") == "premium"
    assert harness.model_lane("claude-opus-4") == "premium"
    assert harness.model_lane("ft:custom-lora") == "finetune"
    assert harness.is_free_or_normal("mystery-model") is False
    allowed = harness.filter_free_normal(
        [
            {"model": "gpt-4o", "kind": "freeroute"},
            {"model": "deepseek-chat", "kind": "freeroute"},
            {"model": "secret-ft", "kind": "finetune"},
        ]
    )
    assert [row["model"] for row in allowed] == ["deepseek-chat"]


def test_source_is_netie_native_not_finetune_or_framework_paste() -> None:
    src = HARNESS_PY.read_text(encoding="utf-8")
    assert "import langgraph" not in src
    assert "from langgraph" not in src
    assert "import langchain" not in src
    assert "from langchain" not in src
    assert "LIVE_KEY" not in src
    assert "openpyxl" not in src.lower()
    assert "import packs" not in src
    assert "from packs" not in src
    assert "torch" not in src
    assert "lora" in src  # ban marker, not an import


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _branch_name() -> str:
    from_ci = os.environ.get("GITHUB_HEAD_REF", "").strip()
    if from_ci:
        return from_ci
    result = _git("branch", "--show-current")
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_exact_c_stamp_289_seam(path: str) -> bool:
    """Allow only #289's frozen stamp diff on #289's branch."""
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


def _is_exact_c7_05_104p2_seam(path: str) -> bool:
    """Allow only #104p2's frozen ask-route L2 gate seam."""
    allowed = _C7_05_104P2_BRANCH_EXCEPTIONS.get(_branch_name(), frozenset())
    if path not in allowed:
        return False
    expected = _C7_05_104P2_EXACT_DIFF.get(path)
    if expected is None:
        return False
    diff = _git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        f"{_C7_05_104P2_BASE}..HEAD",
        "--",
        path,
    )
    assert diff.returncode == 0, diff.stderr
    expected_digest, seam_symbols = expected
    for symbol in seam_symbols:
        assert symbol in diff.stdout, f"#104p2 L2 gate seam missing {symbol!r} in {path}"
    stable_diff = "\n".join(
        line for line in diff.stdout.splitlines() if not line.startswith("index ")
    )
    digest = hashlib.sha256(f"{stable_diff}\n".encode()).hexdigest()
    assert digest == expected_digest, (
        f"#104p2 exception covers only its exact ask-route L2 gate seam in {path}; "
        "any other edit needs its own exception"
    )
    return True


def _is_exact_c7_06_105_seam(path: str) -> bool:
    """Allow only #105 C7-06's exact L1 serve-chooser seam on its branch."""
    allowed = _C7_06_105_BRANCH_EXCEPTIONS.get(_branch_name(), frozenset())
    if path not in allowed:
        return False
    expected = _C7_06_105_EXACT_DIFF.get(path)
    if expected is None:
        return False
    diff = _git(
        "diff",
        "--no-ext-diff",
        "--unified=0",
        f"{_C7_06_105_BASE}..HEAD",
        "--",
        path,
    )
    assert diff.returncode == 0, diff.stderr
    expected_digest, seam_symbols = expected
    for symbol in seam_symbols:
        assert symbol in diff.stdout, f"#105 C7-06 seam missing {symbol!r} in {path}"
    stable_diff = "\n".join(
        line for line in diff.stdout.splitlines() if not line.startswith("index ")
    )
    digest = hashlib.sha256(f"{stable_diff}\n".encode()).hexdigest()
    assert digest == expected_digest, (
        f"#105 C7-06 exception covers only its exact L1 chooser seam in {path}; "
        "any other edit needs its own exception"
    )
    return True


def test_branch_does_not_dual_write_freeze_or_liberty_or_freeroute() -> None:
    if _git("rev-parse", "--verify", "origin/main").returncode != 0:
        pytest.skip("origin/main missing")
    diff = _git("diff", "--name-only", "origin/main")
    assert diff.returncode == 0, diff.stderr
    names = {line.strip() for line in diff.stdout.splitlines() if line.strip()}
    banned = {
        "CortexOS/crew/freeroute.py",
        "CortexOS/crew/liberty_seek.py",
        "CortexOS/crew/liberty_routes.py",
        # #269 ROUTER-1 owns the FreeRoute store schema/_write_row/_stats/pick.
        "CortexOS/execution/distill_harness.py",
        "CortexOS/dms/answer_engine.py",
        "CortexOS/dms/l2_generation.py",
        "packages/cortex_contract/execution.py",
    }
    overlap = sorted(names & banned)
    held = [
        path
        for path in overlap
        if not (
            _is_exact_c_stamp_289_seam(path)
            or _is_exact_c7_04_288_seam(path)
            or _is_exact_c7_05_104p2_seam(path)
            or _is_exact_c7_06_105_seam(path)
        )
    ]
    assert held == [], f"dual-write of frozen/other-seat files: {held}"


def test_other_branch_editing_answer_engine_still_trips_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "cursor/c7-04-288-other-branch")
    path = "CortexOS/dms/answer_engine.py"
    assert _is_exact_c7_04_288_seam(path) is False


def test_other_branch_editing_c7_05_seam_still_trips_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "cursor/c7-05-other-branch")
    path = "CortexOS/dms/answer_engine.py"
    assert _is_exact_c7_05_104p2_seam(path) is False


def test_other_branch_editing_c7_06_seam_still_trips_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "cursor/c7-06-other-branch")
    path = "CortexOS/dms/answer_engine.py"
    assert _is_exact_c7_06_105_seam(path) is False


@pytest.mark.asyncio
async def test_unarmed_fail_closed_no_invented_percent(crew_env) -> None:
    calls: list[str] = []

    async def boom(*args, **kwargs):  # noqa: ANN001
        calls.append("called")
        raise AssertionError("complete must not run when unarmed")

    out = await harness.run_harness(cases=_cases(boom), complete=boom)
    assert out["status"] == "REFUSE"
    assert out["ok"] is False
    assert out["complete"] is False
    assert out["issue_212_complete"] is False
    assert out["closes_212"] is False
    assert out["measured_baseline"]["gen"] == "57.69%"
    reason = out["refuse_reason"].lower()
    assert "unarmed" in reason
    assert calls == []
    assert out["final"] == "UNARMED"
    assert "99.95" not in str(out)


@pytest.mark.asyncio
async def test_premium_only_candidates_fail_closed(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    monkeypatch.setattr(
        fr,
        "candidates",
        lambda purpose="generative_ask": [
            {"label": "openai", "model": "gpt-4o", "kind": "freeroute"}
        ],
    )
    calls: list[str] = []

    async def boom(*args, **kwargs):  # noqa: ANN001
        calls.append("called")
        raise AssertionError("premium must not be spent")

    out = await harness.run_harness(cases=_cases(boom), complete=boom)
    assert out["status"] == "REFUSE"
    assert out["final"] == "NO_FREE_NORMAL"
    assert calls == []
    assert out["complete"] is False
    assert out["issue_212_complete"] is False


@pytest.mark.asyncio
async def test_measure_reports_fixture_vs_baseline_not_a_better_percent(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed_free(monkeypatch)
    fake = _script("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")
    out = await harness.run_harness(cases=_cases(fake), complete=fake)
    assert out["ok"] is True
    assert out["status"] == "INCOMPLETE"
    assert out["complete"] is False
    assert out["issue_212_complete"] is False
    assert out["closes_212"] is False
    assert out["like_with_like"] is False
    assert out["climb_measured"] is False
    assert out["replaces_baseline"] is False
    assert out["invented_better"] is False
    assert out["measured_baseline"]["gen"] == "57.69%"
    assert out["measured_baseline"]["exact"] == "38.46%"
    assert out["measured_baseline"]["wrong"] == 0
    assert out["this_run"]["n"] == 5
    assert out["this_run"]["validated"] == 2
    assert out["this_run"]["wrong"] == 0
    assert out["this_run"]["gen"] == "40.00%"
    assert out["this_run"]["exact"] == "0.00%"
    assert out["vs_baseline"]["baseline_gen"] == "57.69%"
    assert out["vs_baseline"]["this_run_gen"] == "40.00%"
    assert out["vs_baseline"]["this_run_exact"] == "0.00%"
    assert out["vs_baseline"]["improved"] is False
    assert out["this_run"]["gen"] != "57.69%"
    assert "99.95" not in str(out)
    assert out["issue_212"] == "OPEN/INCOMPLETE"
    assert out["distill"]["ideas_only"] is True
    assert out["distill"]["gpu_finetune"] is False
    assert out["distill"]["climb_complete"] is False
    assert out["gpu_finetune"] is False
    assert out["models"][0]["lane"] in {"free", "normal"}


@pytest.mark.asyncio
async def test_distill_complete_does_not_stamp_climb_complete(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed_free(monkeypatch)
    fake = _script("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")
    out = await harness.run_harness(
        cases=_cases(fake),
        complete=fake,
        distill_recipe={"option_id": "gencfsm_dag"},
    )
    assert out["distill"]["status"] == "COMPLETE"
    assert out["distill"]["ideas_only"] is True
    assert out["status"] == "INCOMPLETE"
    assert out["complete"] is False
    assert out["issue_212_complete"] is False
    assert out["closes_212"] is False


@pytest.mark.asyncio
async def test_refuse_invent_complete_gpu_jepa_live_host_and_better_percent(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed_free(monkeypatch)
    fake = _script("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")
    complete = await harness.run_harness(claim_complete=True, complete=fake)
    assert complete["status"] == "REFUSE"
    assert "212" in complete["refuse_reason"]
    assert complete["issue_212_complete"] is False
    assert complete["closes_212"] is False

    gpu = await harness.run_harness(gpu_finetune=True, complete=fake)
    assert gpu["status"] == "REFUSE"
    assert "finetune" in gpu["refuse_reason"].lower()

    jepa = await harness.run_harness(trained_jepa=True, complete=fake)
    assert jepa["status"] == "REFUSE"
    assert "jepa" in jepa["refuse_reason"].lower()

    live = await harness.run_harness(live_5000_ci=True, live_8020_ci=True, complete=fake)
    assert live["status"] == "REFUSE"
    assert "5000" in live["refuse_reason"] or "8020" in live["refuse_reason"]
    assert live["live_5000_ci"] is False

    better = await harness.run_harness(claimed_gen="99.95%", complete=fake)
    assert better["status"] == "REFUSE"
    assert "better" in better["refuse_reason"].lower()

    paste = await harness.run_harness(paste_langgraph=True, complete=fake)
    assert paste["status"] == "REFUSE"
    assert "langgraph" in paste["refuse_reason"].lower()

    distill_paste = await harness.run_harness(
        distill_recipe={"option_id": "langchain", "engine_role": "product_engine"},
        complete=fake,
    )
    assert distill_paste["status"] == "REFUSE"


def test_http_get_stamps_incomplete_and_post_refuses_invent_complete(client) -> None:
    law = client.http.get("/crew/prompt-harness").json()
    assert law["execute"] == "POST /crew/prompt-harness"
    assert law["status"] == "INCOMPLETE"
    assert law["complete"] is False
    assert law["issue_212_complete"] is False
    assert law["closes_212"] is False
    assert law["measured_baseline"]["gen"] == "57.69%"
    assert law["live_5000_ci"] is False
    insights_law = client.http.get("/crew/insights").json()
    assert insights_law["prompt_harness"]["status"] == "INCOMPLETE"
    assert insights_law["cot_climb"]["status"] == "INCOMPLETE"

    refused = client.http.post("/crew/prompt-harness", json={"claim_complete": True})
    assert refused.status_code == 200
    body = refused.json()
    assert body["status"] == "REFUSE"
    assert body["complete"] is False
    assert body["issue_212_complete"] is False
    assert body["closes_212"] is False

    unarmed = client.http.post("/crew/prompt-harness", json={})
    assert unarmed.status_code == 200
    env = unarmed.json()
    assert env["status"] == "REFUSE"
    assert "unarmed" in env["refuse_reason"].lower()
    assert env["measured_baseline"]["exact"] == "38.46%"
    assert env["live_5000_ci"] is False
    dumped = str(env) + str(law)
    assert "99.95" not in dumped
    assert "LIVE_KEY" not in dumped


@pytest.mark.asyncio
async def test_pinned_26_like_with_like_stays_incomplete(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import cot_climb

    _armed_free(monkeypatch)
    fake = _script("", "", "")
    out = await harness.run_harness(
        corpus=cot_climb.LIKE_WITH_LIKE_CORPUS,
        complete=fake,
    )
    assert out["ok"] is True
    assert out["like_with_like"] is True
    assert out["climb_measured"] is True
    assert out["this_run"]["n"] == 26
    assert out["this_run"]["gen"] == "0.00%"
    assert out["this_run"]["gen"] != "57.69%"
    assert out["this_run"]["exact"] == "0.00%"
    assert out["this_run"]["exact"] != "38.46%"
    assert out["vs_baseline"]["this_run_exact"] == "0.00%"
    assert out["vs_baseline"]["improved"] is False
    assert out["complete"] is False
    assert out["status"] == "INCOMPLETE"
    assert out["issue_212_complete"] is False
    assert out["closes_212"] is False
    assert out["replaces_baseline"] is False
    assert out["invented_better"] is False
    assert out["issue_212"] == "OPEN/INCOMPLETE"
    assert "99.95" not in str(out)
