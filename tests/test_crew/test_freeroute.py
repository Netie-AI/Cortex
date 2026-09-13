"""CORTEX-OV-FREEROUTE: central OpenVault layer. No live :5000. No invent-green keys."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import freeroute as fr
from CortexOS.crew import openvault
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def _live_vault(monkeypatch: pytest.MonkeyPatch, labels: dict[str, dict] | None = None) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setattr(
        openvault, "healthz", lambda timeout=1.5: {"ok": True, "url": "http://127.0.0.1:5000"}
    )
    monkeypatch.setattr(openvault, "vault_sources", lambda: labels or {})
    monkeypatch.setattr(openvault, "list_models", lambda timeout=1.5: [])
    monkeypatch.setattr(
        openvault, "ratelimit", lambda identity, timeout=1.2: {"ok": True, "identity": identity}
    )


def test_identity_is_stable_and_returns_no_token(client) -> None:
    body = client.http.get("/crew/identity").json()
    assert body["identity"] == "cortex:crew"
    assert body["mint"] is False
    assert body["token_returned"] is False
    assert body["custody"] == "openvault"
    assert body["live_5000_ci"] is False
    assert body["measured_baseline"]["cite"].startswith("DMS #180")
    assert body["measured_baseline"]["gen"] == "57.69%"
    assert body["measured_baseline"]["exact"] == "38.46%"
    assert body["measured_baseline"]["wrong"] == 0
    dumped = str(body).lower()
    assert "sk-" not in dumped
    assert "gsk-" not in dumped
    assert "live_key" not in dumped
    health = client.http.get("/crew/health").json()
    assert health["cortex_identity"]["identity"] == "cortex:crew"
    assert health["cortex_identity"]["token_returned"] is False


def test_unarmed_complete_fail_closed(crew_env) -> None:
    import asyncio

    out = asyncio.run(fr.complete(prompt="think about sku count", purpose="think"))
    assert out["ok"] is False
    assert out["status"] == "REFUSE"
    assert out["values"] == []
    refused = str(out.get("refused") or "")
    assert "unarmed" in refused.lower() or "CREW_OPENVAULT" in refused
    assert out["live_5000_ci"] is False


def test_http_freeroute_unarmed_is_409(client) -> None:
    res = client.http.post("/crew/freeroute", json={"purpose": "think", "prompt": "hello"})
    assert res.status_code == 409
    body = res.json()
    assert body["ok"] is False
    assert body["values"] == []
    assert body["live_5000_ci"] is False
    status = client.http.get("/crew/freeroute").json()
    assert status["armed"] is False
    assert status["live_5000_ci"] is False
    assert status["chosen"] is None


def test_pick_route_measured_not_hardcoded_grok(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {
            "deepseek": {
                "id": "k-ds",
                "label": "DEEPSEEK_API_KEY",
                "enabled": True,
                "crew_label": "deepseek",
            },
            "groq": {
                "id": "k-g",
                "label": "GROQ_API_KEY",
                "enabled": True,
                "crew_label": "groq",
            },
            "cursor": {
                "id": "k-c",
                "label": "CURSOR_API_KEY",
                "enabled": True,
                "crew_label": "cursor",
            },
        },
    )
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-value-do-not-leak")
    fr.reset_measurements()
    cands = fr.candidates()
    labels = {row["label"] for row in cands}
    assert "deepseek" in labels
    assert "groq" in labels
    assert "cursor" in labels
    auto = fr.pick_route(purpose="insights")
    assert auto.model == "auto"
    assert auto.label == "openvault"
    assert "hardcode" in auto.why.lower() or "auto" in auto.why.lower()
    assert auto.identity == "cortex:crew:insights"
    fr.record_measurement("deepseek", latency_ms=40, ok=True)
    fr.record_measurement("groq", latency_ms=400, ok=True)
    fr.record_measurement("cursor", latency_ms=80, ok=False)
    picked = fr.pick_route(purpose="generative_ask")
    assert picked.label == "deepseek"
    assert picked.label != "cursor"
    assert picked.identity == "cortex:crew:generative-ask"
    assert picked.score is not None
    assert picked.score != 57.69
    assert "57.69" not in picked.why


def test_pick_route_single_armed_family(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {
            "qwen": {
                "id": "k-q",
                "label": "qwen",
                "enabled": True,
                "crew_label": "qwen",
            }
        },
    )
    picked = fr.pick_route(purpose="think")
    assert picked.label == "qwen"
    assert "qwen" in picked.model.lower() or picked.model == "qwen2.5-7b-instruct"
    assert picked.identity == "cortex:crew:think"


def test_validate_sql_ontology_fail_closed() -> None:
    allowed = {"inventory", "shipments"}
    ok = fr.validate_sql(
        "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory", allowed
    )
    assert ok["ok"] is True
    assert ok["sql"]
    bad = fr.validate_sql("SELECT * FROM secrets", allowed)
    assert bad["ok"] is False
    assert bad["sql"] is None
    ddl = fr.validate_sql("DROP TABLE inventory", allowed)
    assert ddl["ok"] is False
    extracted = fr.extract_sql("there are 999 skus\n```sql\nSELECT sku FROM inventory\n```")
    assert extracted is not None
    assert "999" not in extracted
    assert "inventory" in extracted.lower()


@pytest.mark.asyncio
async def test_complete_armed_uses_identity_header(
    crew_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {
            "deepseek": {
                "id": "k-ds",
                "label": "DEEPSEEK_API_KEY",
                "enabled": True,
                "crew_label": "deepseek",
            }
        },
    )
    seen: list[dict[str, Any]] = []

    async def fake_chat(messages, **kwargs):  # noqa: ANN001
        seen.append({"messages": messages, **kwargs})
        from CortexOS.crew.llm import LLMResult

        return LLMResult(text="Think: ontology first.", model="deepseek-chat")

    monkeypatch.setattr(openvault, "chat", fake_chat)
    out = await fr.complete(prompt="how many skus", purpose="think")
    assert out["ok"] is True
    assert out["values"] == []
    assert out["identity"] == "cortex:crew:think"
    assert out["live_5000_ci"] is False
    assert out["measured_baseline"]["wrong"] == 0
    assert seen[0]["identity"].startswith("cortex:crew")
    assert seen[0]["measured"] is True
    dumped = str(out)
    assert "cursor-test-value" not in dumped


def test_resolve_ov_model_measured_keeps_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-value-do-not-leak")
    monkeypatch.delenv("CREW_OPENVAULT_MODEL", raising=False)
    assert openvault.resolve_ov_model("auto") == "grok-4.6"
    assert openvault.resolve_ov_model("auto", measured=True) == "auto"
    assert openvault.resolve_ov_model("openvault/auto", measured=True) == "auto"
