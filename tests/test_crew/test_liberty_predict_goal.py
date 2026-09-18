"""LIBERTY-PREDICT-GOAL: plan language + proxy V(s,a,g). Refuse invent-trained forecasts."""

from __future__ import annotations

import json
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
    assert body["issue_225_complete"] is False
    assert body["issue_224_complete"] is False
    assert body["issue_212_complete"] is False
    assert body["jepa"]["mode"] == "proxy"
    assert body["jepa"]["trained"] is False
    assert body["jepa"]["world_model"] is False
    assert body["forecast"]["mode"] == "proxy"
    assert body["forecast"]["trained"] is False
    assert body["forecast"]["trained_forecast"] is False
    assert body["forecast"]["future_observation"] is False
    assert body["forecast"]["source"] == liberty_seek.VALUE_SOT
    assert body["forecast"]["language"] == "plan"
    assert body["value_sot"] == liberty_seek.VALUE_SOT
    assert body["predict"] == "POST /crew/liberty/predict-goal"
    assert body["cot_climb"]["status"] == "INCOMPLETE"
    assert body["measured_baseline"]["gen"] == "57.69%"
    assert body["measured_baseline"]["exact"] == "38.46%"
    assert body["measured_baseline"]["wrong"] == 0
    assert body["live_5000_ci"] is False
    assert body["live_8020_ci"] is False
    assert body["langgraph"] is False
    blob = json.dumps(body).lower()
    assert "trained world model complete" not in blob
    assert "trained jepa complete" not in blob
    assert "trained jepa forecast" not in blob or "not a trained jepa forecast" in blob


def test_operator_predict_goal_uses_plan_language_and_proxy_v(client) -> None:
    res = client.http.post(
        "/crew/liberty/predict-goal",
        json={"statement": "Grow monthly revenue ethically", "execute": False},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "PLAN"
    assert body["ok"] is True
    assert body["executed"] == []
    plan = body["plan"]
    assert plan["language"] == "plan"
    assert plan["trained_forecast"] is False
    assert plan["future_observation"] is False
    assert "V(s,a,g)" in plan["text"]
    assert "not a trained JEPA forecast" in plan["text"]
    assert plan["steps"]
    assert all(step["language"] == "plan" for step in plan["steps"])
    assert "PLAN" in body["answer"]
    assert "V(s,a,g)" in body["answer"]
    values = body["values"]
    assert values
    for row in values:
        assert row["s"]
        assert row["a"]
        assert row["g"] == body["goal_id"]
        assert row["mode"] == "proxy"
        assert row["trained"] is False
        assert row["forecast"] is False
        assert row["sot"] == liberty_seek.VALUE_SOT
        assert row["value"] is not None
    ranked = [p["value"] for p in body["proposals"]]
    assert ranked == sorted(ranked, reverse=True)
    assert body["audit"]["ok"] is True
    assert body["audit"]["event"] == "engine.seek"
    _assert_honesty(body)
    text = liberty_seek.render_predict_text(body)
    assert "status: PLAN" in text
    assert "plan_language: plan" in text
    assert "trained_forecast: False" in text
    assert "V(s,a,g):" in text
    assert body["proposals"][0]["title"] in text
    assert any("proxy tabular V(s,a,g)" in a for a in body["assumptions"])


def test_no_goal_refuses_predict_without_inventing_forecast() -> None:
    body = liberty_seek.predict_goal()
    assert body["status"] == "REFUSE"
    assert body["reason"] == "no_goal_bound"
    assert body["proposals"] == []
    assert body["values"] == []
    assert body["plan"] is None
    assert body["forecast"]["trained_forecast"] is False
    text = liberty_seek.render_predict_text(body)
    assert "status: REFUSE" in text
    assert "trained_forecast: False" in text
    _assert_honesty(body)


def test_proxy_only_jepa_still_plans_without_trained_forecast(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("jepa absent")

    monkeypatch.setattr(liberty_seek, "collapse_score", boom)
    body = liberty_seek.predict_goal(statement="Grow monthly revenue ethically")
    assert body["status"] in ("PLAN", "PARK")
    assert body["collapse"]["ok"] is False
    assert body["jepa"]["trained"] is False
    assert body["forecast"]["trained_forecast"] is False
    assert body["forecast"]["future_observation"] is False
    assert body["plan"]["language"] == "plan"
    assert body["plan"]["trained_forecast"] is False
    assert "V(s,a,g)" in body["plan"]["text"]
    assert body["values"]
    assert all(row["trained"] is False and row["forecast"] is False for row in body["values"])
    blob = json.dumps(body).lower()
    assert "trained world model complete" not in blob
    text = liberty_seek.render_predict_text(body)
    assert "trained_forecast: False" in text
    _assert_honesty(body)


def test_refuse_invent_trained_forecast_claims() -> None:
    forced = liberty_seek.forecast_stamp(
        extra={
            "trained": True,
            "trained_forecast": True,
            "future_observation": True,
            "world_model": True,
            "complete": True,
            "mode": "trained",
        }
    )
    assert forced["trained"] is False
    assert forced["trained_forecast"] is False
    assert forced["future_observation"] is False
    assert forced["world_model"] is False
    assert forced["complete"] is False
    assert forced["mode"] == "proxy"
    assert forced["language"] == "plan"
    for kwargs in (
        {"trained": True},
        {"forecast": True},
        {"future_observation": True},
    ):
        refused = liberty_seek.predict_goal(
            statement="Grow monthly revenue ethically", **kwargs
        )
        assert refused["status"] == "REFUSE"
        assert refused["reason"] == "invent_trained_forecast"
        assert refused["proposals"] == []
        assert refused["values"] == []
        assert refused["executed"] == []
        assert "trained JEPA forecast" in refused["answer"]
        assert eg.list_goals() == []
        _assert_honesty(refused)


def test_http_refuses_trained_forecast_flag(client) -> None:
    res = client.http.post(
        "/crew/liberty/predict-goal",
        json={"statement": "Grow monthly revenue ethically", "forecast": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "REFUSE"
    assert body["reason"] == "invent_trained_forecast"
    assert body["forecast"]["trained_forecast"] is False
    assert body["live_5000_ci"] is False


def test_execute_true_parks_predict_plan() -> None:
    body = liberty_seek.predict_goal(
        statement="Keep the warehouse honest",
        execute=True,
    )
    assert body["status"] == "PARK"
    assert body["ok"] is False
    assert body["reason"] == "execute_parked"
    assert body["executed"] == []
    assert body["plan"]["language"] == "plan"
    assert body["values"]
    assert body["forecast"]["trained_forecast"] is False
    assert "Not a trained JEPA forecast" in body["answer"]
    _assert_honesty(body)


def test_get_map_lists_predict_goal_and_control_does_not_post(client) -> None:
    body = client.http.get("/crew/liberty").json()
    assert body["ok"] is True
    assert body["predict"] == "POST /crew/liberty/predict-goal"
    assert body["execute"] == "POST /crew/liberty/seek"
    assert body["display_only"] is True
    assert "plan language" in body["agents"]
    assert "V(s,a,g)" in body["agents"]
    _assert_honesty(body)
    control = client.http.get("/crew/appshell/control").json()
    assert control["liberty"]["predict"] == "POST /crew/liberty/predict-goal"
    assert control["liberty"]["forecast"]["trained_forecast"] is False
    posted = client.http.post("/crew/appshell/control")
    assert posted.status_code == 405
    cat = appshell.catalog(engine_url="http://127.0.0.1:8010")
    assert cat["liberty"]["predict"] == "POST /crew/liberty/predict-goal"
    assert cat["liberty"]["value_sot"] == liberty_seek.VALUE_SOT
    assert cat["liberty"]["forecast_trained"] is False


def test_buyer_ui_has_predict_goal() -> None:
    html = UI.read_text(encoding="utf-8")
    assert 'id="libertyPredict"' in html
    assert "Predict goal" in html
    assert "/crew/liberty/predict-goal" in html
    assert "V(s,a,g)" in html
    assert "plan language" in html
    src = Path(liberty_seek.__file__).read_text(encoding="utf-8")
    for banned in ("from langgraph", "import langchain", "import n8n", "from n8n", "mybot"):
        assert banned not in src.lower()
        assert banned not in html.lower()
    assert "trained world model complete" not in src.lower()
    assert "trained jepa complete" not in src.lower()


def test_predict_goal_calls_action_value(monkeypatch) -> None:
    calls: list[int] = []
    real = action_value.value

    def wrapped(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(action_value, "value", wrapped)
    body = liberty_seek.predict_goal(statement="Grow monthly revenue ethically")
    assert body["status"] == "PLAN"
    assert calls, "predict-goal must call action_value.value for V(s,a,g)"
    assert body["values"]
    assert body["plan"]["sot"] == liberty_seek.VALUE_SOT


@pytest.mark.asyncio
async def test_tool_lands_predict_plan_in_transcript(rig) -> None:
    space = rig.store.create_space("Liberty predict")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "cortex_liberty_predict_goal",
                        statement="Grow monthly revenue ethically",
                    )
                ]
            ),
            LLMResult(text="PLAN next steps with proxy V(s,a,g). Not a trained forecast."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "predict this goal")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools
    assert "status: PLAN" in tools[0]["content"]
    assert "V(s,a,g):" in tools[0]["content"]
    assert "trained_forecast: False" in tools[0]["content"]
    env = (tools[0]["meta"] or {}).get("envelope") or {}
    assert env["status"] == "PLAN"
    assert env["plan"]["language"] == "plan"
    assert env["forecast"]["trained_forecast"] is False
    assert env["values"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "PLAN" in answer["content"] or "V(s,a,g)" in answer["content"]
