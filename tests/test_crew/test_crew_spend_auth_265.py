"""#265 TRUST-CONTRACT-AUTH item 3 (P24.4): Crew never spends for an unauthenticated caller.

Every Crew route that can start or wake a model call (a crew run through
``crew/llm.py`` or a FreeRoute generate) authenticates through the engine auth
port before the handler runs. Local (loopback) callers are not exempt: every
refusal here is sent from a ``127.0.0.1`` peer, the one peer the FreeRoute
relay used to wave through without a credential.

Each test counts the stand-in provider: ``crew.llm.chat`` (the crew run's model
call), ``crew.llm._litellm`` (the SDK under it) and the scripted OpenVault's
``chat_calls`` (FreeRoute). A refused call leaves every count at exactly 0. A
caller with a valid key then sees the reply the operator would read.

POST /crew/freeroute is not covered: tests/test_crew/test_freeroute.py (a
#215-guarded file this ticket may not edit) still pins a keyless loopback spend
on it. That needs a founder decision.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import llm as llm_mod
from CortexOS.crew import openvault
from CortexOS.crew.llm import LLMResult
from CortexOS.crew.server import create_app
from CortexOS.integrations import freeroute as core
from CortexOS.security.auth_port import SPEND_REQUIRES_AUTH
from tests.api_key_isolation import TEST_VIEWER_KEY

LOOPBACK = ("127.0.0.1", 5555)
VIEWER = {"X-API-Key": TEST_VIEWER_KEY}


class _CountingChat:
    """Stand-in for the crew model call. Counts every call; answers ``Ready.``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, model: str, messages: list[dict[str, Any]], **_kw: Any) -> LLMResult:
        self.calls.append({"model": model, "messages": messages})
        return LLMResult(text="Ready.", model=model)


class _CountingSdk:
    def __init__(self) -> None:
        self.calls = 0

    async def acompletion(self, **_kw: Any) -> Any:
        self.calls += 1
        raise AssertionError("no provider SDK call may happen in this test")


@pytest.fixture()
def spend(settings, crew_env, monkeypatch) -> Iterator[SimpleNamespace]:
    """Crew app over the production chat hook, with every spend counted."""
    chat = _CountingChat()
    sdk = _CountingSdk()
    monkeypatch.setattr(llm_mod, "chat", chat)
    monkeypatch.setattr(llm_mod, "_litellm", lambda: sdk)
    app = create_app(settings)  # no llm_chat: the runtime binds llm_mod.chat
    with TestClient(app, client=LOOPBACK) as tc:
        yield SimpleNamespace(http=tc, chat=chat, sdk=sdk, crew=app.state.crew)


def _mute_key_api(monkeypatch) -> None:
    monkeypatch.setattr(openvault, "ingest_cursor_from_files", lambda root=None: {"ok": False})
    monkeypatch.setattr(openvault, "push_env_keys", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(openvault, "disable_seeded_cortex_primary", lambda: {"ok": True})
    monkeypatch.setattr(openvault, "list_vault_keys", lambda **kw: [])


@pytest.fixture()
def armed(settings, crew_env, armed_openvault, monkeypatch) -> Iterator[SimpleNamespace]:
    """FreeRoute armed against the scripted vault; the peer is loopback."""
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    _mute_key_api(monkeypatch)
    core.reset()
    chat = _CountingChat()
    monkeypatch.setattr(llm_mod, "chat", chat)
    app = create_app(settings)
    with TestClient(app, client=LOOPBACK) as tc:
        yield SimpleNamespace(http=tc, chat=chat, vault=armed_openvault)


def _assert_spend_refused(res: Any, status: int = 401) -> None:
    assert res.status_code == status, res.text
    detail = res.json()["detail"]
    assert detail["code"] == SPEND_REQUIRES_AUTH
    assert "local callers are not exempt" in detail["message"]


def _wait_idle(crew: Any, space_id: str) -> None:
    deadline = time.time() + 5
    while crew.runtime._space_run.get(space_id):
        if time.time() > deadline:
            raise AssertionError("run did not finish")
        time.sleep(0.02)


def test_loopback_message_without_a_key_spends_nothing_then_a_viewer_is_answered(spend) -> None:
    space = spend.http.post("/crew/spaces", json={"title": "HQ"}).json()

    for headers in ({}, {"X-API-Key": "not-a-configured-key"}):
        refused = spend.http.post(
            f"/crew/spaces/{space['id']}/messages", json={"text": "hello"}, headers=headers
        )
        _assert_spend_refused(refused)
        _wait_idle(spend.crew, space["id"])
        assert spend.chat.calls == []
        assert spend.sdk.calls == 0
        # No side effect: the refused text never reached the transcript.
        assert spend.http.get(f"/crew/spaces/{space['id']}/messages").json() == []

    posted = spend.http.post(
        f"/crew/spaces/{space['id']}/messages", json={"text": "hello"}, headers=VIEWER
    )
    assert posted.status_code == 200, posted.text
    assert "run_id" in posted.json()
    _wait_idle(spend.crew, space["id"])
    msgs = spend.http.get(f"/crew/spaces/{space['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["content"] == "Ready."
    assert len(spend.chat.calls) == 1
    assert spend.sdk.calls == 0


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/crew/spaces/{sid}/agents", {"name": "Scout", "brief": "look around"}),
        ("/crew/spaces/{sid}/agents/{aid}/accept", {"brief": "go"}),
        ("/crew/assign", {"destination": "crew", "space_id": "{sid}", "brief": "x", "execute": True}),
        ("/crew/tickets/claim", {"spec": "Netie-AI/Cortex#1", "space_id": "{sid}"}),
        ("/crew/tickets/build", {"spec": "Netie-AI/Cortex#1", "space_id": "{sid}"}),
        ("/crew/tickets/scale", {"space_id": "{sid}", "limit": 1}),
    ],
)
def test_loopback_run_starters_refuse_without_a_key_before_any_model_call(
    spend, path: str, body: dict[str, Any]
) -> None:
    space = spend.http.post("/crew/spaces", json={"title": "HQ"}).json()
    sid = space["id"]
    manager = next(
        a for a in spend.http.get(f"/crew/spaces/{sid}/agents").json() if a["name"] == "Manager"
    )
    agents_before = spend.http.get(f"/crew/spaces/{sid}/agents").json()
    url = path.format(sid=sid, aid=manager["id"])
    payload = {k: (v.format(sid=sid) if isinstance(v, str) else v) for k, v in body.items()}

    res = spend.http.post(url, json=payload)

    _assert_spend_refused(res)
    _wait_idle(spend.crew, sid)
    assert spend.chat.calls == []
    assert spend.sdk.calls == 0
    assert spend.crew.runtime._space_run.get(sid) is None
    assert spend.http.get(f"/crew/spaces/{sid}/agents").json() == agents_before
    assert spend.http.get(f"/crew/spaces/{sid}/messages").json() == []


def test_loopback_insights_generate_without_a_key_sends_nothing_to_freeroute(armed) -> None:
    armed.vault.reply("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")

    res = armed.http.post("/crew/insights", json={"intent": "how many skus", "generate": True})

    _assert_spend_refused(res)
    assert armed.vault.chat_calls == []
    assert armed.chat.calls == []

    # Only the generate branch spends: the keyless ontology preview is unchanged.
    preview = armed.http.post("/crew/insights", json={"intent": "how many skus", "ask": False})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["phase"] == "ontology"
    assert body["ontology"]["locations"]
    assert armed.vault.chat_calls == []


def test_loopback_prompt_harness_without_a_key_sends_nothing_to_freeroute(armed) -> None:
    armed.vault.reply("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")

    res = armed.http.post("/crew/prompt-harness", json={})

    _assert_spend_refused(res)
    assert armed.vault.chat_calls == []
    assert armed.chat.calls == []
