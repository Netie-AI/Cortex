"""A default DMS install still works against the #263 gates.

DMS calls Cortex with the key it is configured with. By default that is the
published ``dms-demo-viewer-key``, sent as both ``X-API-Key`` and
``Authorization: Bearer`` (``tests/test_api_security/test_dms_demo_key_callers.py``
cites the DMS client lines). ``DMS_API_KEYS`` is unset on a default install.

One test per DMS call path. Each sends DMS's real header shape with a body the
route serves, and asserts 200 plus the body the caller reads. The F5 paths come
from ``docs/dms/packets/CORTEX_TO_DMS_C4_SUBMIT.md`` section 5: DMS calls
``/dms/tasks/gate/check`` on every mutation and fails closed when it is refused.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.dms.test_rag03_answer_routing import dms_http  # noqa: F401 - fixture

DEMO_KEY = "dms-demo-viewer-key"
DMS_HEADERS = {"X-API-Key": DEMO_KEY, "Authorization": f"Bearer {DEMO_KEY}"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from CortexOS.execution import app_store, submit

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "dms_ops.db"))
    monkeypatch.setattr(submit, "refresh_jwks", lambda: {"ok": True, "stub": True})
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _event(task_id: str = "restock") -> str:
    from packs.dms.tasks.gate import create_task_event

    return create_task_event(
        message_id=None,
        thread_id=None,
        task_id=task_id,
        intent="restock",
        filled_template={"sku": "SKU-T263", "qty": 3},
        actor="dms_seed",
    )


def test_f5_gate_check_is_served_to_the_dms_key(client):
    event_id = _event()
    res = client.post(
        "/dms/tasks/gate/check",
        json={"event_id": event_id, "task_id": "restock", "filled_template": {"sku": "SKU-T263", "qty": 3}},
        headers=DMS_HEADERS,
    )
    assert res.status_code == 200, res.text
    assert res.json()["event_id"] == event_id
    assert set(res.json()) == {"status", "violations", "executable", "event_id"}


def test_gate_acknowledge_is_not_a_dms_call_and_refuses_the_demo_key(client):
    """DMS does not call acknowledge, so it stays steward on the engine port."""
    event_id = _event()
    res = client.post("/dms/tasks/gate/acknowledge", json={"event_id": event_id}, headers=DMS_HEADERS)
    assert res.status_code == 401
    assert res.json() == {"detail": "Valid API key required (X-API-Key or Bearer)"}


def test_f5_gate_refuses_an_anonymous_caller(client):
    event_id = _event()
    res = client.post("/dms/tasks/gate/check", json={"event_id": event_id, "task_id": "restock"})
    assert res.status_code == 401


def test_contract_ask_is_served_to_the_dms_key(dms_http, monkeypatch):  # noqa: F811
    """A bound session, as DMS has after submitting its signed manifest."""
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    dms_http.bind_session("dms-t263", {"inventory": "TRUE"})
    res = dms_http.post(
        "/v1/contract/ask",
        json={"question": "how many inventory rows are there", "session_id": "dms-t263"},
        headers=DMS_HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("audit_id")
    assert "answer" in body or "provenance" in body


def test_contract_ledger_append_is_served_to_the_dms_key(client):
    res = client.post(
        "/v1/contract/ledger/append",
        json={"actor": "dms_service", "event_type": "dms.t263.probe", "payload": {"n": 1}},
        headers=DMS_HEADERS,
    )
    assert res.status_code == 200, res.text
    assert res.json()["event_type"] == "dms.t263.probe"


@pytest.mark.parametrize("path", ["/v1/insights", "/v1/insights/keys", "/v1/insights/identity"])
def test_insights_reads_are_served_to_the_dms_key(client, path):
    res = client.get(path, headers=DMS_HEADERS)
    assert res.status_code == 200, res.text


def test_insights_rank_is_served_to_the_dms_key(client):
    res = client.post("/v1/insights", json={"intent": "stock by sku", "generate": False}, headers=DMS_HEADERS)
    assert res.status_code == 200, res.text


def test_dms_query_is_served_to_the_dms_key(client):
    res = client.post("/dms/query", json={"question": "total stock on hand"}, headers=DMS_HEADERS)
    assert res.status_code == 200, res.text
    assert res.json()["audit_id"]


# DMS's Trust page (apps/api/dms_api/routes/trust.py) and ontology proxy
# (routes/ontology.py) read these. Cortex does not serve them today, so DMS
# gets 404, as on main. A gate here would turn that into 401.
@pytest.mark.parametrize(
    "path",
    ["/dms/eval/summary", "/dms/eval/runs/nightly", "/dms/ontology", "/dms/ontology/graph"],
)
def test_dms_proxied_reads_answer_404_not_401(client, path):
    res = client.get(path, headers=DMS_HEADERS)
    assert res.status_code == 404, res.text


def test_contract_drillthrough_is_untouched_by_263(client):
    """Out of scope (#265): the DMS key and an anonymous caller get the same answer as on main."""
    with_key = client.post("/v1/contract/drillthrough", json={}, headers=DMS_HEADERS)
    anonymous = client.post("/v1/contract/drillthrough", json={})
    assert with_key.status_code == anonymous.status_code == 422
    assert with_key.json() == anonymous.json()
