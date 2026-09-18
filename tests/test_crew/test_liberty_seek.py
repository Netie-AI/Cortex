"""LIBERTY-SEEK: G2.1 seeker on Control+Crew. Start vs refuse/park. No live-host green."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import appshell, liberty_seek
from CortexOS.crew.llm import LLMResult
from CortexOS.crew.server import create_app
from CortexOS.execution import (
    action_event,
    action_value,
    app_store,
    commitments,
    goal_audit,
    routine_scheduler,
    scoreboard,
)
from CortexOS.execution import enterprise_goal as eg
from tests.test_crew.conftest import FakeLLM, wait_run_done
from tests.test_crew.test_runtime import _tc

UI = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "ui" / "index.html"


@pytest.fixture(autouse=True)
def _isolate_seeker(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "DB_PATH", tmp_path / "goals.db")
    monkeypatch.setattr(routine_scheduler, "DB_PATH", tmp_path / "routines.db")
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    monkeypatch.setattr(action_value, "DB_PATH", tmp_path / "action_value.db")
    monkeypatch.setattr(goal_audit, "LEDGER_DB_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(commitments, "DB_PATH", tmp_path / "commitments.db")
    monkeypatch.setattr(action_event, "DB_PATH", tmp_path / "action_events.db")
    from CortexOS.audit import register_ledger
    from packs.dms.audit import ledger as dms_ledger

    register_ledger(dms_ledger)
    eg.init()
    routine_scheduler.init()
    scoreboard.init()
    app_store.init()
    action_value.init()


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def _assert_honesty(body: dict[str, Any]) -> None:
    assert body["complete"] is False
    assert body["issue_223_complete"] is False
    assert body["jepa"]["trained"] is False
    assert body["jepa"]["world_model"] is False
    assert body["cot_climb"]["status"] == "INCOMPLETE"
    assert body["cot_climb"]["invented_better"] is False
    assert body["measured_baseline"]["gen"] == "57.69%"
    assert body["measured_baseline"]["exact"] == "38.46%"
    assert body["measured_baseline"]["wrong"] == 0
    assert body["measured_baseline"]["replaces_baseline"] is False
    assert body["live_5000_ci"] is False
    assert body["live_8020_ci"] is False
    assert body["langgraph"] is False
    assert body["executed"] == [] if "executed" in body else True
    assert body["engine"] == "CortexOS.execution.seeker.seek"


def test_get_map_is_display_only_and_does_not_seek(client) -> None:
    body = client.http.get("/crew/liberty").json()
    assert body["ok"] is True
    assert body["execute"] == "POST /crew/liberty/seek"
    assert body["display_only"] is True
    assert body["spawn"] is False
    assert body["control_spawn"] is False
    assert "Start liberty seek" in body["agents"]
    assert body["bound_goal"] is None
    _assert_honesty(body)
    assert eg.list_goals() == []
    posted = client.http.post("/crew/liberty")
    assert posted.status_code == 405


def test_operator_start_runs_g21_seek_with_audit_trail(client) -> None:
    res = client.http.post(
        "/crew/liberty/seek",
        json={"statement": "Grow monthly revenue ethically", "execute": False},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "SEEK"
    assert body["ok"] is True
    assert body["initiative"] == "proactive"
    assert body["executed"] == []
    assert len(body["proposals"]) >= 1
    assert all(p["title"] and p["why"] for p in body["proposals"])
    assert "SEEK" in body["answer"]
    assert "Propose-only" in body["answer"]
    audit = body["audit"]
    assert audit["ok"] is True
    assert audit["event"] == "engine.seek"
    assert eg.get_goal(body["goal_id"]) is not None
    seeks = eg.list_seeks(body["goal_id"])
    assert seeks
    assert seeks[0]["proposals"]
    _assert_honesty(body)
    text = liberty_seek.render_tool_text(body)
    assert "status: SEEK" in text
    assert "executed_count: 0" in text
    assert body["proposals"][0]["title"] in text
    assert "jepa: proxy (not trained)" in text


def test_no_goal_refuses_without_inventing_autonomy(client) -> None:
    body = liberty_seek.start_seek()
    assert body["status"] == "REFUSE"
    assert body["reason"] == "no_goal_bound"
    assert body["proposals"] == []
    assert body["executed"] == []
    assert "invent" in body["answer"].lower() or "no bound" in body["answer"].lower()
    http = client.http.post("/crew/liberty/seek", json={})
    assert http.status_code == 200
    refused = http.json()
    assert refused["status"] == "REFUSE"
    assert refused["proposals"] == []
    assert refused["executed"] == []
    _assert_honesty(refused)
    text = liberty_seek.render_tool_text(refused)
    assert "status: REFUSE" in text
    assert "no_goal_bound" in text


def test_execute_true_parks_and_does_not_run(client) -> None:
    body = liberty_seek.start_seek(
        statement="Keep the warehouse honest",
        execute=True,
    )
    assert body["status"] == "PARK"
    assert body["ok"] is False
    assert body["reason"] == "execute_parked"
    assert body["executed"] == []
    assert len(body["parked"]) >= 1
    assert len(body["proposals"]) >= 1
    assert body["audit"]["ok"] is True
    assert body["audit"]["event"] == "engine.seek"
    _assert_honesty(body)
    text = liberty_seek.render_tool_text(body)
    assert "status: PARK" in text
    assert "executed_count: 0" in text
    http = client.http.post(
        "/crew/liberty/seek",
        json={"statement": "Keep the warehouse honest", "execute": True},
    )
    parked = http.json()
    assert parked["status"] == "PARK"
    assert parked["executed"] == []


def test_ledger_miss_parks_without_invented_trail(monkeypatch) -> None:
    from CortexOS.audit import ledger_registry

    ledger_registry.clear_ledger()
    monkeypatch.setattr(ledger_registry, "_load_active_pack", lambda: None)
    body = liberty_seek.start_seek(statement="Grow monthly revenue ethically")
    assert body["status"] == "PARK"
    assert body["reason"] == "audit_unavailable"
    assert body["executed"] == []
    assert len(body["proposals"]) >= 1
    assert body["ok"] is False
    text = liberty_seek.render_tool_text(body)
    assert "status: PARK" in text
    assert "executed_count: 0" in text
    assert "invent" in body["answer"].lower() or "did not invent" in body["answer"].lower()


def test_unknown_goal_and_missing_extra_refuse(client, monkeypatch) -> None:
    missing = liberty_seek.start_seek(goal_id="goal-does-not-exist")
    assert missing["status"] == "REFUSE"
    assert missing["reason"] == "unknown_goal"
    assert missing["proposals"] == []
    monkeypatch.setenv("CORTEX_PROFILE", "core")
    gated = liberty_seek.start_seek(statement="A goal that cannot run without agentic")
    assert gated["status"] == "REFUSE"
    assert gated["reason"] == "extra_missing"
    assert gated["proposals"] == []
    assert gated["executed"] == []
    assert eg.list_goals() == []


def test_control_get_stamps_liberty_and_does_not_post(client) -> None:
    control = client.http.get("/crew/appshell/control").json()
    assert control["display_only"] is True
    assert control["banner"] == "Display only F-0030"
    lib = control["liberty"]
    assert lib["display_only"] is True
    assert lib["spawn"] is False
    assert lib["complete"] is False
    assert lib["jepa"]["trained"] is False
    assert lib["live_5000_ci"] is False
    assert lib["live_8020_ci"] is False
    posted = client.http.post("/crew/appshell/control")
    assert posted.status_code == 405
    cat = appshell.catalog(engine_url="http://127.0.0.1:8010")
    assert cat["liberty"]["control_spawn"] is False
    assert cat["liberty"]["jepa_trained"] is False
    assert cat["liberty"]["complete"] is False


def test_buyer_ui_has_start_seek_and_control_display() -> None:
    html = UI.read_text(encoding="utf-8")
    assert 'data-nav="liberty"' in html
    assert 'id="plane-liberty"' in html
    assert 'id="libertySeek"' in html
    assert "Start seek" in html
    assert 'id="controlLiberty"' in html
    assert "/crew/liberty/seek" in html
    assert "langgraph" not in html.lower()
    assert "langchain" not in html.lower()
    src = Path(liberty_seek.__file__).read_text(encoding="utf-8")
    for banned in ("from langgraph", "import langchain", "import n8n", "from n8n", "mybot"):
        assert banned not in src.lower()
        assert banned not in html.lower()


@pytest.mark.asyncio
async def test_tool_lands_seek_text_in_transcript(rig) -> None:
    space = rig.store.create_space("Liberty")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[_tc("cortex_liberty_seek", statement="Grow monthly revenue ethically")]
            ),
            LLMResult(text="SEEK next steps are drafted. Nothing executed."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "start liberty seek")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools
    assert "status: SEEK" in tools[0]["content"]
    assert "executed_count: 0" in tools[0]["content"]
    env = (tools[0]["meta"] or {}).get("envelope") or {}
    assert env["status"] == "SEEK"
    assert env["executed"] == []
    assert len(env["proposals"]) >= 1
    assert env["audit"]["ok"] is True
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "SEEK" in answer["content"]
    assert "executed" in answer["content"].lower() or "draft" in answer["content"].lower()
