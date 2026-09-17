"""CORTEX-OV-FREEROUTE: crew over the one Cortex layer. No live :5000. No invent-green keys.

The crew module is an async adapter: arming, candidates, the measured route and
the credential live in CortexOS.integrations.freeroute. Armed cases here drive
the production chain against the scripted OpenVault (tests/conftest.py fixtures)
and assert the chat call count, so none can pass on a refusal branch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import freeroute as fr
from CortexOS.crew import openvault
from CortexOS.crew.server import create_app
from CortexOS.integrations import freeroute as core
from tests.test_crew.conftest import FakeLLM

CALLER_KEY = "ov_callerkeyxxxxxxxx"
CORTEX_KEY = "ov_cortexkeyxxxxxxxx"


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def _mute_key_api(monkeypatch) -> None:
    """Silence crew's key-management calls (rows, upsert, seeded primary).

    Those speak to the OpenVault key API over crew's own httpx client and are
    not the spend path under test; with CREW_OPENVAULT=1 they would otherwise
    open real sockets to :5000 (the conftest guard fails the test if they do).
    """
    monkeypatch.setattr(openvault, "ingest_cursor_from_files", lambda root=None: {"ok": False})
    monkeypatch.setattr(openvault, "push_env_keys", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(openvault, "disable_seeded_cortex_primary", lambda: {"ok": True})
    monkeypatch.setattr(openvault, "list_vault_keys", lambda **kw: [])


@pytest.fixture()
def armed_client(settings, crew_env, armed_openvault, monkeypatch) -> Iterator[SimpleNamespace]:
    """Crew app with FreeRoute armed against the scripted vault, peer = loopback."""
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    _mute_key_api(monkeypatch)
    core.reset()
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app, client=("127.0.0.1", 5555)) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew, vault=armed_openvault)


def _only_models(fake, models: list[str], provider: str = "groq") -> None:
    fake.hops = [fake.hop(provider, 10)]
    fake.catalogue = {provider: list(models)}


# -- unchanged surfaces ----------------------------------------------------------


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
    # The label carries no authority; OpenVault decides who the caller is.
    assert body["authority"] is False
    assert body["credential"]["authority"] is False
    dumped = str(body).lower()
    assert "sk-" not in dumped
    assert "gsk-" not in dumped
    assert "live_key" not in dumped
    health = client.http.get("/crew/health").json()
    assert health["cortex_identity"]["identity"] == "cortex:crew"
    assert health["cortex_identity"]["token_returned"] is False


def test_unarmed_complete_fail_closed(crew_env) -> None:
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


def test_validate_sql_ontology_fail_closed() -> None:
    allowed = {"inventory", "shipments"}
    ok = fr.validate_sql("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory", allowed)
    assert ok["ok"] is True
    assert ok["sql"]
    bad = fr.validate_sql("SELECT * FROM secrets", allowed)
    assert bad["ok"] is False
    assert bad["sql"] is None
    ddl = fr.validate_sql("DROP TABLE inventory", allowed)
    assert ddl["ok"] is False
    # A comma join is a join: the old regex allowlist passed this one.
    comma = fr.validate_sql("SELECT sku FROM inventory, secrets", allowed)
    assert comma["ok"] is False
    assert "secrets" in comma["reason"]
    assert fr.validate_sql("SELECT 42 AS revenue", allowed)["ok"] is False
    assert fr.validate_sql("SELECT sku FROM inventory", set())["ok"] is False
    extracted = fr.extract_sql("there are 999 skus\n```sql\nSELECT sku FROM inventory\n```")
    assert extracted is not None
    assert "999" not in extracted
    assert "inventory" in extracted.lower()


# -- measured route (rewritten: production calls, served-model scoring) ------------


def test_pick_route_measured_not_hardcoded_grok(armed_openvault, monkeypatch) -> None:
    """Rewritten for #211 follow-up.

    The old test armed from /api/keys rows plus CURSOR_API_KEY in the process env,
    asserted the multi-family pick was label "openvault" / model "auto" (a pseudo
    label the scoreboard never scored), and reached the measured branch only
    through the record_measurement test hook. Now: candidates come from live hops
    x OpenVault catalogue, exploration is real, and the pick follows validity
    verdicts recorded through production complete() calls.
    """
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-value-do-not-leak")
    _only_models(armed_openvault, ["fast-but-wrong", "slower-but-valid"])
    labels = [row["model"] for row in fr.candidates(purpose="generative_ask")]
    assert labels == ["fast-but-wrong", "slower-but-valid"]
    assert "grok-4.6" not in labels

    async def ask() -> dict[str, Any]:
        return await fr.complete(prompt="how many skus", purpose="generative_ask")

    asked: list[str] = []
    for _ in range(4):
        out = asyncio.run(ask())
        assert out["ok"] is True, out
        stamp = out["stamp"]
        asked.append(stamp["requested"])
        core.note_verdict(
            stamp["call_id"], "gate_pass" if stamp["served"] == "slower-but-valid" else "gate_fail"
        )
    assert asked == ["fast-but-wrong", "fast-but-wrong", "slower-but-valid", "slower-but-valid"]
    picked = fr.pick_route(purpose="generative_ask")
    assert picked.model == "slower-but-valid"
    assert picked.identity == "cortex:crew:generative-ask"
    assert "57.69" not in picked.why
    assert len(armed_openvault.chat_calls) == 4


def test_pick_route_single_armed_family(armed_openvault) -> None:
    """Rewritten: one spendable hop, candidates from that hop's catalogue."""
    _only_models(armed_openvault, ["qwen/qwen3.6-27b", "openai/gpt-oss-20b"], provider="groq")
    picked = fr.pick_route(purpose="think")
    assert picked.model == "qwen/qwen3.6-27b"
    assert picked.label == "groq"
    assert picked.identity == "cortex:crew:think"


def test_complete_armed_sends_no_identity_header_and_stamps_served(armed_openvault) -> None:
    """Rewritten: the #214 test asserted crew passed a self-described identity
    header and measured=True to a monkeypatched openvault.chat. OpenVault never
    reads such a header (A-0009), so it must be absent, and the stamp must say
    which model actually served."""
    armed_openvault.hops = [armed_openvault.hop("groq", 10)]
    armed_openvault.reply("Think: ontology first.")
    out = asyncio.run(fr.complete(prompt="how many skus", purpose="think"))
    assert out["ok"] is True
    assert out["values"] == []
    assert out["identity"] == "cortex:crew:think"
    assert out["live_5000_ci"] is False
    assert len(armed_openvault.chat_calls) == 1
    call = armed_openvault.chat_calls[0]
    assert "X-Cortex-Identity" not in call["headers"]
    assert "Authorization" not in call["headers"]  # no Cortex key configured
    served = armed_openvault.served_for(call["body"]["model"])
    assert out["model"] == served
    assert out["stamp"]["served"] == served
    assert f"served {served}" in out["stamp"]["line"]
    assert "cursor-test-value" not in str(out)


def test_resolve_ov_model_measured_keeps_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rewritten: an env key must not pick the FreeRoute model at all."""
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-test-value-do-not-leak")
    monkeypatch.delenv("CREW_OPENVAULT_MODEL", raising=False)
    assert openvault.resolve_ov_model("auto") == "auto"
    assert openvault.resolve_ov_model("auto", measured=True) == "auto"
    assert openvault.resolve_ov_model("openvault/auto", measured=True) == "auto"


def test_crew_kill_switch_refuses_even_when_the_core_would_arm(
    armed_openvault, monkeypatch
) -> None:
    """CREW_OPENVAULT=0 is the crew operator's own off switch: the vault is
    armed and reachable here, and crew still sends nothing."""
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    assert core.arming(fresh=True).armed is True
    assert fr.arming()["armed"] is False
    out = asyncio.run(fr.complete(prompt="how many skus", purpose="think"))
    assert out["ok"] is False
    assert "CREW_OPENVAULT=0" in out["refused"]
    assert armed_openvault.chat_calls == []


def test_crew_chat_connector_goes_through_the_core(armed_openvault) -> None:
    """The crew chat connector spends through the core, not its own httpx POST."""
    _only_models(armed_openvault, ["openai/gpt-oss-120b"])
    armed_openvault.reply("pong")
    result = asyncio.run(openvault.chat([{"role": "user", "content": "ping"}], model="auto"))
    assert result.text == "pong"
    assert result.model == "openai/gpt-oss-120b"
    assert len(armed_openvault.chat_calls) == 1
    assert armed_openvault.chat_calls[0]["body"]["model"] == "openai/gpt-oss-120b"
    assert "X-Cortex-Identity" not in armed_openvault.chat_calls[0]["headers"]


def test_crew_chat_refusal_names_the_openvault_status(armed_openvault) -> None:
    from CortexOS.crew.llm import LLMError

    _only_models(armed_openvault, ["openai/gpt-oss-120b"])
    armed_openvault.reply(status=503, error_type="openvault_no_keys", message="no healthy keys")
    with pytest.raises(LLMError) as exc:
        asyncio.run(openvault.chat([{"role": "user", "content": "ping"}], model="auto"))
    assert "no candidate hop (HTTP 503)" in str(exc.value)


# -- G3 on the status surface ------------------------------------------------------


def test_status_refuses_sealed_vault_with_env_key_and_ollama(armed_client, monkeypatch) -> None:
    """A sealed vault that still lists key rows, plus an env key and a local
    Ollama, armed the #214 layer. Only OpenVault's status arms now."""
    monkeypatch.setenv("GROQ_API_KEY", "sk-" + "z" * 30)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "1")
    armed_client.vault.sealed = True
    body = armed_client.http.get("/crew/freeroute").json()
    assert body["armed"] is False
    assert "sealed" in body["arming"]["detail"]
    assert body["chosen"] is None
    assert armed_client.vault.non_openvault_calls == []


def test_status_never_serves_vault_key_rows(armed_client) -> None:
    body = armed_client.http.get("/crew/freeroute").json()
    dumped = str(body)
    assert body["armed"] is True
    assert "kid-groq" not in dumped
    assert "GROQ_API_KEY" not in dumped
    assert "gsk_leakedkeyfragment123" not in dumped


def test_health_adds_no_freeroute_probe(armed_client, monkeypatch) -> None:
    """Health still pings OpenVault reachability once for the UI (F-0040); the
    credential block reads the last arming instead of probing again."""
    monkeypatch.setattr(
        openvault, "healthz", lambda timeout=1.5: {"ok": True, "url": "http://127.0.0.1:5000"}
    )
    # The provider chain arms from OpenVault status (one probe, cached); the
    # credential block on health adds none of its own.
    armed_client.http.get("/crew/freeroute")
    before = len(armed_client.vault.calls)
    body = armed_client.http.get("/crew/health").json()
    assert len(armed_client.vault.calls) == before
    assert body["cortex_identity"]["credential"]["authority"] is False
    assert body["cortex_identity"]["arming"]["armed"] in (True, False)


# -- A-0009: caller authority ------------------------------------------------------


def test_cortex_key_is_never_lent_to_an_http_caller(armed_client, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", CORTEX_KEY)
    armed_client.vault.identities[CORTEX_KEY] = "key_cortex"
    core.reset()
    for _ in range(2):
        res = armed_client.http.post(
            "/crew/freeroute",
            json={"purpose": "think", "prompt": "hello"},
            headers={"X-Cortex-Identity": "attacker:root"},
        )
        assert res.status_code == 200, res.text
    sent = armed_client.vault.chat_calls
    assert len(sent) == 2
    for call in sent:
        assert "Authorization" not in call["headers"]
        assert "X-Cortex-Identity" not in call["headers"]


def test_caller_bearer_is_relayed_verbatim(armed_client) -> None:
    armed_client.vault.identities[CALLER_KEY] = "key_caller"
    res = armed_client.http.post(
        "/crew/freeroute",
        json={"purpose": "think", "prompt": "hello"},
        headers={"Authorization": f"Bearer {CALLER_KEY}"},
    )
    assert res.status_code == 200, res.text
    assert armed_client.vault.chat_calls[-1]["headers"]["Authorization"] == f"Bearer {CALLER_KEY}"


def test_non_loopback_caller_without_a_key_is_refused_before_spending(
    settings, crew_env, armed_openvault, monkeypatch
) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    _mute_key_api(monkeypatch)
    core.reset()
    app = create_app(settings, llm_chat=FakeLLM())
    with TestClient(app, client=("10.0.0.5", 5555)) as remote:
        res = remote.post("/crew/freeroute", json={"purpose": "think", "prompt": "hello"})
        assert res.status_code == 401
        body = res.json()
        assert body["values"] == []
        assert "needs its own OpenVault ov_ key" in body["refused"]
        assert armed_openvault.chat_calls == []
        gen = remote.post("/crew/insights", json={"intent": "how many skus", "generate": True})
        assert gen.status_code == 401
        assert armed_openvault.chat_calls == []


def test_mcp_child_env_never_inherits_the_cortex_key(monkeypatch) -> None:
    from CortexOS.crew import mcp_client, shell

    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", CORTEX_KEY)
    spec = mcp_client.MCPServerSpec(name="probe", command=["python", "-c", "pass"], armed=True)
    seen: dict[str, Any] = {}

    async def fake_exec(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        raise RuntimeError("stop before spawning")

    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError):
        asyncio.run(mcp_client.MCPClient(spec).start())
    assert seen, "spawn env was not captured"
    assert "CORTEX_FREEROUTE_TOKEN" not in seen
    assert "CORTEX_FREEROUTE_TOKEN" not in shell.isolate_env(None, forward_host=True)
