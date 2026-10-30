"""LANE-ROUTER-01 on a pick turn: the router never runs, the held reading does.

Contract 1.10.0 (PICK-READING-01): a request carrying ``plan_sql_reading_id``
runs its held reading, and every 200 to a pick carries that id. With
``CORTEX_LANE_ROUTER=1`` the router is skipped on a pick (``skipped:pick``), so
a ``documents`` decision can never replace the held reading with a document
answer. Asserted on the served envelope of ``POST /v1/contract/ask``.
"""

# ruff: noqa: F811  (pytest fixtures are imported, then requested by name)
from __future__ import annotations

from typing import Any

import pytest

from bench.lanes import assert_contract_answer, lane_line
from CortexOS.dms import lane_ask
from tests.test_plan_sql.test_plan_sql_pick_reading import (  # noqa: F401
    GRANT,
    SESSION,
    SPLIT,
    _assert_served_pick,
    _deterministic_sample,
    _json,
    _offer,
    _pick,
    _plan_sql_on,
    _readings,
    _readings_on,
    _script_split,
    dms_http,
    execute_spy,
)

DOCUMENTS = '{"lane":"documents"}'
DOC_REPLY = "Acme invoices are payable Net 30 from the invoice date [1]."


def test_pick_skips_the_router_and_serves_the_held_reading(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(lane_ask.ENABLED_ENV, raising=False)
    _script_split(armed_openvault, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)
    reading = _readings(_offer(dms_http))[0]
    calls = len(armed_openvault.chat_calls)

    # Were the router to run, it would be told "documents" and given a cited reply.
    monkeypatch.setenv(lane_ask.ENABLED_ENV, "1")
    armed_openvault.reply(DOCUMENTS).reply(DOC_REPLY)
    resp = _pick(dms_http, reading["reading_id"])
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = _json(resp)
    assert_contract_answer(body)

    found = lane_line(body)
    assert found is not None and found.first, body["assumptions"]
    assert body["assumptions"][0] == "lane: skipped (skipped:pick; calls=0)"
    assert len(armed_openvault.chat_calls) == calls
    served = {**body, "assumptions": body["assumptions"][1:]}
    _assert_served_pick(served, SPLIT[0], reading["reading_id"], reading["statement"], 1, 3)
    assert body["provenance"]["badge"] != "document"
