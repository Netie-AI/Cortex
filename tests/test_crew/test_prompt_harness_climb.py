"""EPIC-PROMPT-HARNESS-CLIMB: measure vs #180; refuse invent-COMPLETE / GPU / trained."""

from __future__ import annotations

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
        "packages/cortex_contract/execution.py",
    }
    # #272 LOCAL-1 is seated for the core arming/stamp/LOCAL_ONLY slice.
    # Freeze / liberty / contract / crew adapter files stay banned.
    if (ROOT / "CortexOS" / "integrations" / "freeroute_ov_local.py").is_file():
        banned.discard("CortexOS/integrations/freeroute.py")
    overlap = sorted(names & banned)
    assert overlap == [], f"dual-write of frozen/other-seat files: {overlap}"


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
