"""CREW-INSIGHTS: ontology where+importance, then certify/refuse with audit fields."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import insights
from CortexOS.crew.llm import LLMResult
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM, wait_run_done
from tests.test_crew.test_runtime import _tc

INSIGHTS_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "insights.py"


class ScriptedBridge:
    def __init__(self, replies: list[dict[str, Any]] | dict[str, Any]) -> None:
        self.asked: list[str] = []
        self._replies = replies if isinstance(replies, list) else [replies]

    async def ask(self, question: str) -> dict[str, Any]:
        self.asked.append(question)
        idx = min(len(self.asked) - 1, len(self._replies) - 1)
        return dict(self._replies[idx])


def _certified_engine(**extra: Any) -> dict[str, Any]:
    body = {
        "ok": True,
        "answer": "There are 12 skus.",
        "badge": "governed_metric",
        "layer": "governed_metric",
        "metric_id": "sku_count",
        "audit_id": "audit-sku",
        "row_count": 1,
        "rows": [{"sku_count": 12}],
        "sources": ["inventory"],
        "sql_used": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
        "truncated": False,
    }
    body.update(extra)
    return body


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def test_ontology_ranks_location_and_importance_before_ask() -> None:
    ranking = insights.retrieve_ontology("how many skus")
    assert ranking["phase"] == "ontology"
    assert ranking["ok"] is True
    tables = [row["where"]["table"] for row in ranking["locations"]]
    assert "inventory" in tables
    assert ranking["locations"][0]["importance"]["rank"] == 1
    assert ranking["locations"][0]["importance"]["score"] > 0
    metric_ids = [row["id"] for row in ranking["metrics"]]
    assert "sku_count" in metric_ids
    top_metric = ranking["metrics"][0]
    assert top_metric["importance"]["rank"] == 1
    assert "synonym" in top_metric["importance"]["why"]
    assert "answer" not in ranking
    assert ranking.get("values") is None


def test_unknown_intent_has_no_path() -> None:
    ranking = insights.retrieve_ontology("what is our ARR this quarter")
    assert ranking["ok"] is False
    assert ranking["locations"] == []
    assert ranking["metrics"] == []
    assert "no ontology path" in ranking["refuse_reason"]


@pytest.mark.asyncio
async def test_unknown_intent_refuses_without_engine_call() -> None:
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights("what is our ARR this quarter", bridge=bridge, ask=True)
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert bridge.asked == []
    assert "no ontology path" in env["answer"]
    assert env["validation"]["unsure"]
    assert env["validation"]["include"] is not None
    assert env["validation"]["exclude"] is not None


@pytest.mark.asyncio
async def test_certified_envelope_has_include_exclude_unsure() -> None:
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "CERTIFIED"
    assert env["ok"] is True
    assert env["audit_id"] == "audit-sku"
    assert env["values"] == [{"sku_count": 12}]
    assert "12" in env["answer"]
    val = env["validation"]
    assert val["include"]
    assert val["exclude"]
    assert val["unsure"]
    assert any(row["id"] in {"sku_count", "inventory"} for row in val["include"])
    assert any("trial" in (row.get("why") or "").lower() or row.get("kind") == "metric" for row in val["include"])
    assert env["ontology"]["locations"]
    assert env["ontology"]["metrics"]
    assert env["phase"] == "ask"
    assert env["export_runtime"]["prefer"] == "cloudflare-computer"
    assert env["export_runtime"]["heavy_export"] is False
    assert "not COMPLETE" in env["scale"]
    assert bridge.asked
    text = insights.render_tool_text(env)
    assert "status: CERTIFIED" in text
    assert "include:" in text
    assert "exclude:" in text
    assert "unsure:" in text
    assert "12" in text


@pytest.mark.asyncio
async def test_refuse_when_audit_id_missing() -> None:
    bridge = ScriptedBridge(_certified_engine(audit_id=None))
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert any("audit_id" in (row.get("id") or "") for row in env["validation"]["unsure"])


@pytest.mark.asyncio
async def test_refuse_invented_add_vs_rows() -> None:
    bridge = ScriptedBridge(
        _certified_engine(answer="There are 12 skus plus 50 bonus.")
    )
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert "50" not in str(env.get("values"))
    reasons = " ".join(row.get("why") or "" for row in env["validation"]["unsure"])
    assert "invent" in reasons.lower() or "adds" in reasons.lower()


@pytest.mark.asyncio
async def test_abstain_does_not_pad_zero() -> None:
    bridge = ScriptedBridge(
        {
            "ok": True,
            "answer": "Need a warehouse code.",
            "badge": "abstain",
            "layer": "abstain",
            "suggestions": ["how many skus"],
        }
    )
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    assert "0" not in env["answer"] or "Need a warehouse" in env["answer"]
    assert env["validation"]["unsure"]


@pytest.mark.asyncio
async def test_l2_freeform_is_refused() -> None:
    bridge = ScriptedBridge(
        _certified_engine(badge="L2_VALIDATED", layer="generated", metric_id="sku_count")
    )
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "REFUSE"
    assert env["values"] == []


@pytest.mark.asyncio
async def test_ontology_only_does_not_call_engine() -> None:
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights("how many skus", bridge=bridge, ask=False)
    assert env["phase"] == "ontology"
    assert env["status"] is None
    assert env["ontology"]["locations"]
    assert bridge.asked == []
    assert env["values"] == []


@pytest.mark.asyncio
async def test_engine_offline_refuses() -> None:
    bridge = ScriptedBridge(
        {
            "ok": False,
            "answer": "Cortex engine unreachable",
            "badge": "engine_offline",
        }
    )
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert bridge.asked


def test_source_is_ask_spine_not_excel_and_not_complete_claim() -> None:
    src = INSIGHTS_PY.read_text(encoding="utf-8")
    assert "openpyxl" not in src.lower()
    assert "export_pptx" not in src
    assert "workbook" not in src.lower()
    assert "not COMPLETE" in src
    assert "from packs" not in src
    assert "import packs" not in src


def test_http_ontology_then_refuse_offline(client) -> None:
    law = client.http.get("/crew/insights").json()
    assert law["execute"] == "POST /crew/insights"
    assert law["stable"] == "POST /v1/insights"
    assert law["ontology"].startswith("GET /crew/insights/ontology")
    assert law["statuses"] == ["CERTIFIED", "ABSTAIN", "REFUSE"]
    assert "not COMPLETE" in law["scale"]
    assert law["excel_ppt"].startswith("deferred")
    assert "OpenVault" in law["vault"]
    assert "FreeRoute" in law["freeroute"] or "freeroute" in law["freeroute"]
    assert law["measured_baseline"]["gen"] == "57.69%"
    assert law["measured_baseline"]["wrong"] == 0
    assert "cot_climb" in law["generate"]
    assert law["cot_climb"]["complete"] is False
    assert law["cot_climb"]["status"] == "INCOMPLETE"
    assert law["cot_climb"]["issue_212_complete"] is False
    assert law["cot_climb"]["like_with_like"] is False
    assert law["cot_climb"]["measured_baseline"]["gen"] == "57.69%"
    assert law["cot_climb"]["replaces_baseline"] is False
    onto = client.http.get("/crew/insights/ontology", params={"q": "how many skus"}).json()
    assert onto["phase"] == "ontology"
    assert onto["ontology"]["locations"][0]["where"]["table"]
    assert onto["ontology"]["metrics"]
    missing = client.http.get("/crew/insights/ontology")
    assert missing.status_code == 400
    empty = client.http.post("/crew/insights", json={"intent": ""})
    assert empty.status_code == 400
    refused = client.http.post(
        "/crew/insights", json={"intent": "how many skus", "ask": True}
    ).json()
    assert refused["status"] == "REFUSE"
    assert refused["ontology"]["locations"]
    arr = client.http.post(
        "/crew/insights", json={"intent": "what is our ARR", "ask": True}
    ).json()
    assert arr["status"] == "REFUSE"
    assert arr["values"] == []
    gen = client.http.post(
        "/crew/insights", json={"intent": "how many skus", "ask": False, "generate": True}
    ).json()
    assert gen["status"] == "REFUSE"
    assert gen["values"] == []
    assert gen["generative"]["ok"] is False
    climb = gen["generative"].get("climb") or {}
    assert climb.get("complete") is False
    assert climb.get("final") == "UNARMED"


def test_http_certified_with_scripted_bridge(client, monkeypatch) -> None:
    bridge = ScriptedBridge(_certified_engine())
    monkeypatch.setattr(client.crew.bridge, "ask", bridge.ask)
    body = client.http.post(
        "/crew/insights", json={"intent": "how many skus", "ask": True}
    ).json()
    assert body["status"] == "CERTIFIED"
    assert body["validation"]["include"]
    assert body["validation"]["exclude"]
    assert body["validation"]["unsure"]
    assert body["values"][0]["sku_count"] == 12
    assert bridge.asked
    preview = client.http.post(
        "/crew/insights", json={"intent": "how many skus", "ask": False}
    ).json()
    assert preview["phase"] == "ontology"
    assert preview["status"] is None


@pytest.mark.asyncio
async def test_cortex_insights_tool_lands_in_transcript(rig, monkeypatch) -> None:
    bridge = ScriptedBridge(_certified_engine())
    monkeypatch.setattr(rig.runtime.bridge, "ask", bridge.ask)
    space = rig.store.create_space("Data")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("cortex_insights", intent="how many skus")]),
            LLMResult(text="12 skus (CERTIFIED, audit-sku)."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "sku count please")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools
    assert "status: CERTIFIED" in tools[0]["content"]
    assert "include:" in tools[0]["content"]
    env = (tools[0]["meta"] or {}).get("envelope") or {}
    assert env["status"] == "CERTIFIED"
    assert env["validation"]["include"]
    assert env["validation"]["exclude"]
    assert env["validation"]["unsure"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "12" in answer["content"]
    assert "CERTIFIED" in answer["content"]


@pytest.mark.asyncio
async def test_generate_unarmed_fail_closed_no_invented_numbers(crew_env) -> None:
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus", bridge=bridge, ask=False, generate=True
    )
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert bridge.asked == []
    assert env["generative"]["ok"] is False
    assert env["generative"]["values"] == []
    reason = (env.get("answer") or "") + str(env.get("generative"))
    assert "unarmed" in reason.lower() or "invent-green" in reason.lower() or "CREW_OPENVAULT" in reason
    assert "999" not in env["answer"]
    text = insights.render_tool_text(env)
    assert "status: REFUSE" in text


@pytest.mark.asyncio
async def test_generate_armed_sql_abstain_no_numbers(monkeypatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "vault_live": True,
            "local_keys": False,
            "cloud_keys": True,
            "detail": "vault-armed",
            "live_5000_ci": False,
        },
    )

    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        assert purpose in {"think", "generative_ask"}
        if purpose == "generative_ask":
            assert "inventory" in (prompt or "")
        return {
            "ok": True,
            "text": "there are 999 skus\n```sql\nSELECT COUNT(DISTINCT sku) AS sku_count FROM inventory\n```",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
        }

    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus",
        bridge=bridge,
        ask=False,
        generate=True,
        complete=fake_complete,
    )
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    assert env["generative"]["ok"] is True
    assert env["generative"]["valid"] is True
    assert "inventory" in (env["generative"]["sql"] or "").lower()
    assert env["generative"]["identity"] == "cortex:crew:generative-ask"
    assert "999" not in env["answer"]
    assert "999" not in str(env["values"])
    assert bridge.asked == []
    text = insights.render_tool_text(env)
    assert "status: ABSTAIN" in text
    assert "freeroute:" in text
    assert "999" not in text


@pytest.mark.asyncio
async def test_generate_rejects_off_ontology_sql(monkeypatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )

    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, purpose, prompt, kwargs
        return {
            "ok": True,
            "text": "SELECT secret FROM payroll",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
        }

    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus",
        bridge=bridge,
        ask=False,
        generate=True,
        complete=fake_complete,
    )
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert env["generative"]["ok"] is False
    assert "payroll" in str(env["generative"]["refuse_reason"]).lower() or "outside" in str(
        env.get("answer") or ""
    ).lower() or "outside" in str(env["generative"]["refuse_reason"]).lower()


@pytest.mark.asyncio
async def test_generate_plus_ask_attaches_sql_to_certified(monkeypatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )

    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, purpose, prompt, kwargs
        return {
            "ok": True,
            "text": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "qwen", "model": "qwen2.5-7b-instruct"},
        }

    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus",
        bridge=bridge,
        ask=True,
        generate=True,
        complete=fake_complete,
    )
    assert env["status"] == "CERTIFIED"
    assert env["values"] == [{"sku_count": 12}]
    assert env["generative"]["ok"] is True
    assert env["generative"]["sql"]
    assert any(row.get("id") == "generative_sql" for row in env["validation"]["unsure"])
    assert bridge.asked


# -- #211 follow-up G6: the generate validator is the engine guardrail, not a regex --


def _armed(monkeypatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )


def _replies(text: str):
    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, purpose, prompt, kwargs
        return {
            "ok": True,
            "text": text,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "groq", "model": "qwen/qwen3.6-27b"},
            "stamp": {
                "line": "FreeRoute crew-insights-sql: asked qwen/qwen3.6-27b, served "
                "openai/gpt-oss-120b (live hops x OpenVault catalogue)",
                "call_id": "",
                "served": "openai/gpt-oss-120b",
            },
        }

    return fake_complete


async def _generate(monkeypatch, text: str) -> dict:
    _armed(monkeypatch)
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus", bridge=bridge, ask=False, generate=True, complete=_replies(text)
    )
    assert bridge.asked == []
    return env


@pytest.mark.asyncio
async def test_generate_refuses_a_comma_join_onto_an_off_ontology_table(monkeypatch) -> None:
    """The old regex validator passed 'FROM inventory, payroll' as one table."""
    env = await _generate(monkeypatch, "SELECT i.sku FROM inventory, payroll")
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert "payroll" in str(env["generative"]["refuse_reason"])


@pytest.mark.asyncio
async def test_generate_refuses_a_select_with_no_table(monkeypatch) -> None:
    env = await _generate(monkeypatch, "SELECT 42 AS revenue")
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert "42" not in (env.get("answer") or "")


@pytest.mark.asyncio
async def test_generate_names_the_check_that_ran_and_the_served_model(monkeypatch) -> None:
    env = await _generate(
        monkeypatch, "```sql\nSELECT COUNT(DISTINCT sku) AS sku_count FROM inventory\n```"
    )
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    gen = env["generative"]
    assert gen["valid"] is True
    assert gen["validator"].startswith("static sqlglot guardrail")
    assert gen["check"]
    text = insights.render_tool_text(env)
    assert "asked qwen/qwen3.6-27b, served openai/gpt-oss-120b" in text
    assert "static sqlglot guardrail" in text
    unsure = " ".join(row.get("why") or "" for row in env["validation"]["unsure"])
    assert "not executed" in unsure


@pytest.mark.asyncio
async def test_generate_refuses_before_calling_the_model_when_nothing_is_ranked(
    monkeypatch,
) -> None:
    _armed(monkeypatch)
    calls: list[int] = []

    async def fake_complete(messages=None, **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        calls.append(1)
        return {"ok": True, "text": "SELECT sku FROM inventory"}

    bridge = ScriptedBridge(_certified_engine())
    ranking = {"ok": True, "locations": [], "metrics": [], "certified": [], "joins": []}
    monkeypatch.setattr(insights, "retrieve_ontology", lambda *a, **k: ranking)
    env = await insights.run_insights(
        "how many skus", bridge=bridge, ask=False, generate=True, complete=fake_complete
    )
    assert env["status"] == "REFUSE"
    assert calls == []
    assert "ranked ontology tables" in str(env["generative"]["refuse_reason"])
