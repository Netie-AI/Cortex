"""C-LOOP-C (#305) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP bytes and rows DMS receives (R-0001). Flag off, the
response is the 1.4.0 answer byte for byte. Flag on, an answered result gains a
chart spec and an insight whose numbers come from the returned rows; an
abstain stays the same abstain with the same reason. No model is called.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import Answer

from CortexOS.dms import result_package as rp
from tests.dms.test_c_mem_contract_ask import ask_http  # noqa: F401 - pytest fixture

ROOT = Path(__file__).resolve().parents[2]
SESSION = "c-loop-c-305"
TOP_SKUS_Q = "top 5 skus by sales"
REVENUE_Q = "what is our total revenue"
ABSTAIN_QS = ["revenue by month", "Which SKUs are below reorder level?"]
PER_REQUEST_IDS = ("audit_id", "answer_id", "drillthrough_token")
PACKAGE_KEYS = ("chart_spec", "insight")


def _post(client, question: str):
    resp = client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SESSION, "space_id": "alpha"},
    )
    assert resp.status_code == 200, resp.text
    return resp


def _normalized(resp) -> bytes:
    """Raw response bytes with only the per-request random ids masked."""
    raw: bytes = resp.content
    body = resp.json()
    for key in PER_REQUEST_IDS:
        value = body.get(key)
        if isinstance(value, str) and value:
            raw = raw.replace(json.dumps(value).encode(), f'"<{key}>"'.encode())
    return raw


def _ask_both(client, monkeypatch: pytest.MonkeyPatch, question: str):
    monkeypatch.delenv(rp.ENABLED_ENV, raising=False)
    off = _post(client, question)
    monkeypatch.setenv(rp.ENABLED_ENV, "1")
    on = _post(client, question)
    monkeypatch.delenv(rp.ENABLED_ENV, raising=False)
    return off, on


def _sha_rows(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _frozen_1_4_0_answer_properties() -> set[str]:
    spec = json.loads((ROOT / "contract" / "openapi-1.4.0.json").read_text("utf-8"))
    return set(spec["components"]["schemas"]["ContractAnswer"]["properties"])


@pytest.mark.parametrize("question", [TOP_SKUS_Q, REVENUE_Q, *ABSTAIN_QS])
def test_flag_off_contract_ask_is_byte_identical_to_1_4_0(
    ask_http, monkeypatch: pytest.MonkeyPatch, question: str  # noqa: F811
) -> None:
    ask_http.bind_session(SESSION, "alpha")
    monkeypatch.delenv(rp.ENABLED_ENV, raising=False)
    unset = _post(ask_http, question)
    keys = list(unset.json())
    assert keys == [k for k in Answer.model_fields if k not in PACKAGE_KEYS]
    assert set(keys) == _frozen_1_4_0_answer_properties()
    for raw in ("0", "false", "off", ""):
        monkeypatch.setenv(rp.ENABLED_ENV, raw)
        assert _normalized(_post(ask_http, question)) == _normalized(unset), raw


@pytest.mark.parametrize("question", [TOP_SKUS_Q, REVENUE_Q])
def test_flag_on_answered_result_gains_package_from_its_own_rows(
    ask_http, monkeypatch: pytest.MonkeyPatch, question: str  # noqa: F811
) -> None:
    ask_http.bind_session(SESSION, "alpha")
    off, on = _ask_both(ask_http, monkeypatch, question)
    body = on.json()
    rows, sql = body["rows"], body["sql_used"]
    assert rows and sql and body["provenance"]["badge"] not in {"abstain", "blocked"}

    stripped = {k: v for k, v in body.items() if k not in PACKAGE_KEYS}
    off_body = off.json()
    for key in PER_REQUEST_IDS:
        stripped.pop(key, None)
        off_body.pop(key, None)
    assert stripped == off_body, "packaging may only add chart_spec and insight"

    insight, chart = body["insight"], body["chart_spec"]
    assert insight["text"] and insight["served_reason"] is None
    assert rp.untraceable_numbers(insight["text"], rows) == []
    assert str(len(rows)) in insight["text"]
    assert chart["spec"]["data"] == {"name": "rows"}
    assert chart["fields"] and set(chart["fields"]) <= set(rows[0])
    for artifact in (insight, chart):
        assert artifact["served_by"] == rp.SERVED_BY and artifact["served_at"]
        assert artifact["served_rows_sha256"] == _sha_rows(rows)
        assert artifact["served_sql_sha256"] == hashlib.sha256(sql.encode()).hexdigest()
        assert artifact["served_row_count"] == len(rows)
        assert artifact["served_rows_truncated"] is False


def test_flag_on_top_skus_package_names_the_returned_leader(
    ask_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    ask_http.bind_session(SESSION, "alpha")
    _, on = _ask_both(ask_http, monkeypatch, TOP_SKUS_Q)
    body = on.json()
    rows = body["rows"]
    leader = max(rows, key=lambda r: r["sales_value_myr"])
    assert f"(sku {leader['sku']})" in body["insight"]["text"]
    assert body["chart_spec"]["spec"]["mark"] == "bar"
    assert body["chart_spec"]["fields"] == ["sku", "sales_value_myr"]


@pytest.mark.parametrize("question", ABSTAIN_QS)
def test_must_fail_contract_ask_abstain_is_never_packaged(
    ask_http, monkeypatch: pytest.MonkeyPatch, question: str  # noqa: F811
) -> None:
    ask_http.bind_session(SESSION, "alpha")
    _, answered = _ask_both(ask_http, monkeypatch, TOP_SKUS_Q)
    assert answered.json().get("insight"), "control: an answered result is packaged"

    off, on = _ask_both(ask_http, monkeypatch, question)
    body = on.json()
    assert body["provenance"]["badge"] == "abstain"
    assert not any(key in body for key in PACKAGE_KEYS)
    assert body["provenance"]["assumptions"], "the abstain keeps its reason"
    assert body["provenance"] == off.json()["provenance"]
    assert body["answer"] == off.json()["answer"]
    assert _normalized(on) == _normalized(off)
