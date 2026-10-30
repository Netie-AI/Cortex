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
from CortexOS.dms import lane_ask, query_service
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

SPACE = "alpha"
DOCUMENTS = '{"lane":"documents"}'
DOC_TEXT = (
    "Acme invoices are payable Net 30 from the invoice date [1].\nSources: [1] acme_agreement.txt"
)


def test_pick_skips_the_router_and_serves_the_held_reading(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(lane_ask.ENABLED_ENV, raising=False)
    _script_split(armed_openvault, monkeypatch)
    # A Space is bound, so no other skip applies: only the pick skip stands
    # between the router and the held reading.
    dms_http.bind_session(SESSION, GRANT, space_id=SPACE)
    reading = _readings(_offer(dms_http, space_id=SPACE))[0]
    calls = len(armed_openvault.chat_calls)

    # Were the router to run, it would be told "documents" and the document lane
    # would return a cited answer, which would then be served in place of the pick.
    monkeypatch.setenv(lane_ask.ENABLED_ENV, "1")
    armed_openvault.reply(DOCUMENTS)
    monkeypatch.setattr(
        query_service, "rag_answer", lambda *_a, **_k: (DOC_TEXT, ["acme_agreement.txt"])
    )
    resp = _pick(dms_http, reading["reading_id"], space_id=SPACE)
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
