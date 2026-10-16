"""DMS /ask body must-fails. Fixtures only; no symbol added after 0c9cd70c.

Run against that tree and the red is behavioural: the executor body is a 422,
and model, strict, and provider lack the named rejection. A missing import is
not the failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cortex_contract.answer import AskRequest

from CortexOS.integrations import freeroute
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dms_executor_ask.json"
_BODY = json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _detail_item(resp) -> dict:
    detail = resp.json()["detail"]
    if isinstance(detail, list):
        assert detail, resp.text
        return detail[0]
    assert isinstance(detail, dict), resp.text
    return detail


def test_dms_executor_ask_body_matches_main_and_is_not_422(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    """Must-fail: the DMS executor /ask body, tenant_id null included, is not a 422.

    Fixture ``dms_executor_ask`` is the wire body from Netie-AI/dms
    @57d85c529aa363825aaa566f12b8822fd76a4215
    ``packages/executor/dms_executor/__init__.py:889`` (``CortexClient.ask``).
    ``tenant_id`` defaults to None
    (``packages/cortex_client/cortex_client/models.py:17``) and
    ``CortexClient.ask`` (``packages/cortex_client/cortex_client/client.py:115``)
    puts it on the wire. The status must match the same ask with that key
    omitted, which is what main returns for an unbound session.
    """
    sent = dict(_BODY["dms_executor_ask"])
    plain = {
        "question": sent["question"],
        "session_id": sent["session_id"],
        "space_id": sent["space_id"],
    }
    main_status = dms_http.post("/v1/contract/ask", json=plain)
    resp = dms_http.post("/v1/contract/ask", json=sent)
    assert resp.status_code != 422, resp.text
    assert resp.status_code == main_status.status_code, (resp.status_code, resp.text)
    assert resp.status_code == 409, resp.text
    assert armed_openvault.chat_calls == []
    assert armed_openvault.non_openvault_calls == []


@pytest.mark.parametrize("field", list(_BODY["rejected_wire_fields"]))
def test_model_strict_or_provider_is_422_with_zero_complete_calls(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, field: str  # noqa: F811
) -> None:
    """Must-fail: each of model, strict and provider is the named 422.

    The rejection type is ``rejected_wire_field``. ``extra_forbidden`` is the
    old behaviour and is not this rejection. complete() is not called.
    """
    calls: list[object] = []

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise AssertionError("complete() must not run")

    monkeypatch.setattr(freeroute, "complete", _spy)
    assert field not in AskRequest.model_fields
    sent = dict(_BODY["rejected_ask"])
    sent[field] = "gpt"
    resp = dms_http.post("/v1/contract/ask", json=sent)
    assert resp.status_code == 422, resp.text
    item = _detail_item(resp)
    assert item.get("type") == "rejected_wire_field", resp.text
    assert item.get("loc", [None])[-1] == field, resp.text
    assert calls == []
    assert armed_openvault.chat_calls == []
    assert armed_openvault.non_openvault_calls == []
