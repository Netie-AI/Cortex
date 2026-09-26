"""#265 TRUST-CONTRACT-AUTH: role gates on /v1/contract/*, caller ledger actor, no keyless spend.

Items 1, 2 and 4 of the trimmed acceptance, plus the ``/dms/query`` half of
item 3 (the Crew half is in ``tests/test_crew/test_crew_spend_auth_265.py``):

* Every ``/v1/contract/*`` route goes through the engine auth port. Reads (ask,
  drillthrough, tools, ledger/verify) need viewer; submit, ledger/append and
  jwks/refresh need steward. No key is 401 and a too-low role 403, and the
  backend the route calls is never reached (a recording spy stays empty).
* ``ledger/append`` records the authenticated caller, whatever actor the body
  names.
* ``/dms/query`` refuses a caller without a key, loopback included, with the
  named reason ``spend_requires_auth`` before the answer engine or any model
  call runs.
* A caller with a valid key and a sufficient role gets the route's own answer,
  asserted on the rendered answer text and the rows.

Keys are the test-only set from ``tests/api_key_isolation.py``. The in-repo
DMS authorizer is registered for each test and the previous one put back.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from CortexOS.audit import ledger_registry
from CortexOS.security import auth_port
from CortexOS.security.auth_port import SPEND_REQUIRES_AUTH
from tests.api_key_isolation import TEST_ADMIN_KEY, TEST_STEWARD_KEY, TEST_VIEWER_KEY

KEYS = {"viewer": TEST_VIEWER_KEY, "steward": TEST_STEWARD_KEY, "admin": TEST_ADMIN_KEY}
ROLES = ("viewer", "steward", "admin")
LOOPBACK = ("127.0.0.1", 5555)


def _hdr(role: str | None) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]} if role else {}


class _Ledger:
    """A ledger that keeps its rows, so 'no row written' is checked on the chain."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.verified: list[int] = []

    def append(self, actor: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        seq = len(self.rows) + 1
        row = {
            "id": f"row-{seq}",
            "seq": seq,
            "actor": actor,
            "event_type": event_type,
            "payload": payload,
            "prev_hash": "0" * 64,
            "entry_hash": f"{seq:064x}",
            "created_at": "2026-09-26T00:00:00Z",
        }
        self.rows.append(row)
        return row

    def list_entries(self, *, from_seq: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["seq"] >= from_seq][:limit]

    def verify(self, *, start_seq: int = 0) -> dict[str, Any]:
        self.verified.append(start_seq)
        return {"ok": True, "broken_at": None}


@pytest.fixture(autouse=True)
def dms_authorizer():
    from packs.dms.security.api_auth import register_request_authorizer

    previous = auth_port.registered_authorizer()
    register_request_authorizer()
    yield
    if previous is None:
        auth_port.clear_authorizer()
    else:
        auth_port.register_authorizer(previous)


@pytest.fixture
def ledger():
    previous = ledger_registry.registered_ledger()
    stub = _Ledger()
    ledger_registry.register_ledger(stub)
    yield stub
    if previous is None:
        ledger_registry.clear_ledger()
    else:
        ledger_registry.register_ledger(previous)


def _answer(question: str, **_kw: Any) -> dict[str, Any]:
    return {
        "answer": "SKU-T2 has 42 units on hand.",
        "audit_id": "aud-265",
        "route": "metric",
        "badge": "governed_metric",
        "layer": "metric",
        "row_count": 1,
        "rows": [{"sku": "SKU-T2", "qty": 42}],
        "sql_used": None,
    }


@pytest.fixture
def app(monkeypatch, ledger):
    """Contract + /dms routes with every backend they call replaced by a spy."""
    calls: list[str] = []

    def spy(name: str, result: Any):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return result(*args, **kwargs) if callable(result) else result

        return _fn

    from cortex_contract.execution import QueryResult

    import CortexOS.dms.answer_engine as answer_engine
    import CortexOS.dms.query_service as query_service
    import CortexOS.execution.drillthrough as drillthrough
    import CortexOS.execution.session_manifests as session_manifests
    import CortexOS.execution.submit as submit
    import CortexOS.execution.tool_runner as tool_runner

    registry = SimpleNamespace(
        resolve=spy("session.resolve", SimpleNamespace(manifest=None))
    )
    monkeypatch.setattr(session_manifests, "get_session_registry", lambda: registry)
    monkeypatch.setattr(answer_engine, "answer", spy("answer_engine.answer", _answer))
    monkeypatch.setattr(query_service, "answer_question", spy("answer_question", _answer))
    monkeypatch.setattr(
        submit,
        "submit_request",
        spy(
            "submit_request",
            lambda body: QueryResult(ok=True, status="bound", run_id="run-265", output=[{"n": 1}]),
        ),
    )
    monkeypatch.setattr(submit, "refresh_jwks", spy("refresh_jwks", {"ok": True, "keys": 1}))
    monkeypatch.setattr(
        drillthrough,
        "execute_drillthrough",
        spy(
            "execute_drillthrough",
            {
                "answer_id": "a-1",
                "session_id": "s-1",
                "sql_used": "SELECT 1",
                "row_count": 1,
                "rows": [{"sku": "SKU-T2", "qty": 42}],
            },
        ),
    )
    monkeypatch.setattr(
        tool_runner, "allowed_action_tools", spy("allowed_action_tools", {"tool.alpha"})
    )

    from CortexOS.api.contract_routes import register_contract_routes
    from CortexOS.api.dms_query import register_dms_routes

    application = FastAPI()
    application.state.pack = SimpleNamespace(name="dms")
    register_contract_routes(application)
    register_dms_routes(application)
    return SimpleNamespace(app=application, calls=calls, ledger=ledger)


def _client(app: SimpleNamespace) -> TestClient:
    return TestClient(app.app, client=LOOPBACK)


# (method, path, body, minimum role, backend call the handler makes)
ROUTES: list[tuple[str, str, dict[str, Any] | None, str, str]] = [
    ("POST", "/v1/contract/ask", {"question": "how many SKU-T2", "session_id": "s-1"},
     "viewer", "answer_engine.answer"),
    ("POST", "/v1/contract/drillthrough", {"token": "tok-1"}, "viewer", "execute_drillthrough"),
    ("GET", "/v1/contract/tools", None, "viewer", "allowed_action_tools"),
    ("POST", "/v1/contract/ledger/verify", {"start_seq": 0}, "viewer", "ledger.verify"),
    ("POST", "/v1/contract/submit", None, "steward", "submit_request"),
    ("POST", "/v1/contract/ledger/append",
     {"actor": "admin", "event_type": "demo.event", "payload": {"k": "v"}},
     "steward", "ledger.append"),
    ("POST", "/v1/contract/jwks/refresh", None, "steward", "refresh_jwks"),
]


def _send(client: TestClient, method: str, path: str, body: Any, role: str | None) -> Any:
    if method == "GET":
        return client.get(path, headers=_hdr(role))
    return client.post(path, json=body, headers=_hdr(role))


def _reached(app: SimpleNamespace, backend: str) -> bool:
    if backend == "ledger.append":
        return bool(app.ledger.rows)
    if backend == "ledger.verify":
        return bool(app.ledger.verified)
    return backend in app.calls


@pytest.mark.parametrize(("method", "path", "body", "min_role", "backend"), ROUTES)
def test_contract_route_refuses_without_a_key_then_serves_its_role(
    app, monkeypatch, method, path, body, min_role, backend
) -> None:
    if path.endswith("/submit"):
        # SubmitRequest needs a well-formed manifest; build one from the model.
        body = _submit_body()
    client = _client(app)

    for headers in ({}, {"X-API-Key": "not-a-configured-key"}):
        res = client.request(method, path, json=body, headers=headers)
        assert res.status_code == 401, (path, res.status_code, res.text)
        assert not _reached(app, backend), path
        assert app.ledger.rows == []

    for role in ROLES[: ROLES.index(min_role)]:
        low = _send(client, method, path, body, role)
        assert low.status_code == 403, (path, role, low.status_code, low.text)
        assert not _reached(app, backend), path
        assert app.ledger.rows == []

    ok = _send(client, method, path, body, min_role)
    assert ok.status_code == 200, (path, ok.status_code, ok.text)
    assert _reached(app, backend), path


def _submit_body() -> dict[str, Any]:
    from cortex_contract.execution import Manifest, PoolSpec, SubmitRequest

    manifest = Manifest(
        session_id="s-1",
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        issued_at="2026-09-26T00:00:00+00:00",
        expires_at="2026-09-26T00:05:00+00:00",
        signature="sig",
    )
    request = SubmitRequest(
        pool=PoolSpec(id="default"), plan={}, body={"sql": "SELECT 1"}, manifest=manifest
    )
    return request.model_dump(mode="json")


def test_viewer_cannot_append_and_no_row_lands(app) -> None:
    client = _client(app)
    res = client.post(
        "/v1/contract/ledger/append",
        json={"actor": "api_steward", "event_type": "demo.event", "payload": {}},
        headers=_hdr("viewer"),
    )
    assert res.status_code == 403
    assert app.ledger.rows == []
    verify = client.post("/v1/contract/ledger/verify", json={}, headers=_hdr("viewer"))
    assert verify.status_code == 200
    assert verify.json()["ok"] is True
    assert app.ledger.rows == []


@pytest.mark.parametrize("forged", ["admin", "someone_else", "api_viewer"])
@pytest.mark.parametrize("role", ["steward", "admin"])
def test_ledger_append_records_the_caller_not_the_body_actor(app, forged, role) -> None:
    res = _client(app).post(
        "/v1/contract/ledger/append",
        json={"actor": forged, "event_type": "demo.event", "payload": {"k": "v"}},
        headers=_hdr(role),
    )
    assert res.status_code == 200, res.text
    expected = f"api_{role}"
    assert res.json()["actor"] == expected
    assert [r["actor"] for r in app.ledger.rows] == [expected]
    assert app.ledger.rows[0]["event_type"] == "demo.event"
    assert app.ledger.rows[0]["payload"] == {"k": "v"}


def test_contract_ask_serves_a_viewer_the_answer_text_and_rows(app) -> None:
    client = _client(app)
    refused = client.post(
        "/v1/contract/ask", json={"question": "how many SKU-T2", "session_id": "s-1"}
    )
    assert refused.status_code == 401
    assert "answer_engine.answer" not in app.calls

    res = client.post(
        "/v1/contract/ask",
        json={"question": "how many SKU-T2", "session_id": "s-1"},
        headers=_hdr("viewer"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"] == "SKU-T2 has 42 units on hand."
    assert body["rows"] == [{"sku": "SKU-T2", "qty": 42}]
    assert body["provenance"]["badge"] == "governed_metric"
    assert body["audit_id"] == "aud-265"


def test_dms_query_refuses_a_keyless_loopback_caller_before_any_model_call(
    app, armed_openvault
) -> None:
    client = _client(app)
    for headers in ({}, {"X-API-Key": "not-a-configured-key"}):
        res = client.post(
            "/dms/query", json={"question": "how many SKU-T2"}, headers=headers
        )
        assert res.status_code == 401, res.text
        detail = res.json()["detail"]
        assert detail["code"] == SPEND_REQUIRES_AUTH
        assert "local callers are not exempt" in detail["message"]
    assert "answer_question" not in app.calls
    assert armed_openvault.chat_calls == []

    ok = client.post("/dms/query", json={"question": "how many SKU-T2"}, headers=_hdr("viewer"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["answer"] == "SKU-T2 has 42 units on hand."
    assert body["rows"] == [{"sku": "SKU-T2", "qty": 42}]
    assert app.calls.count("answer_question") == 1
