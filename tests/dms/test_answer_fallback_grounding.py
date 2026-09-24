"""TRUST-02 (#258): an engine failure on a grounded call abstains.

``answer_question`` used to route every non-ManifestError exception from the
answer engine to ``_answer_question_legacy``. That path ignores
``require_grounding`` and reads the demo warehouse, so a grounded caller whose
engine call failed received real warehouse rows under no badge.

Assertions are on the rendered answer text, the returned rows and the HTTP body
(CLAUDE.md section 8). The no-false-positive lines prove ungated callers and the
normal engine path are unchanged.
"""

from __future__ import annotations

import re
import sys
from typing import Any

import pytest

from CortexOS.dms import query_service
from CortexOS.execution.manifest import ManifestError

LOW_STOCK_Q = "Which SKUs are below reorder level?"
DELAYED_Q = "show the 5 most delayed shipments"
NO_DOC_Q = "what does SOP-ZZQ9 say about wibblefrobs"
DOC_Q = "according to the supplier contract what are the payment terms"
CANNOT_ANSWER = "could not answer"


class _EngineBoom(RuntimeError):
    pass


@pytest.fixture(scope="module", autouse=True)
def _warehouse_loaded() -> None:
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()


@pytest.fixture
def engine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import CortexOS.dms.answer_engine as ae

    def _boom(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise _EngineBoom("engine exploded near SKU-90003")

    monkeypatch.setattr(ae, "answer", _boom)


@pytest.fixture(scope="module")
def legacy_low_stock() -> dict[str, Any]:
    """What the pre-fix fallback served: the leak we must not reproduce."""
    out = query_service._answer_question_legacy(LOW_STOCK_Q)
    assert out["rows"], "demo warehouse has no low-stock rows; test would be vacuous"
    return out


def _assert_abstain_envelope(body: dict[str, Any], legacy: dict[str, Any]) -> None:
    assert body["rows"] == []
    assert body["sql_used"] is None
    assert body["badge"] == "abstain"
    answer = body["answer"]
    assert CANNOT_ANSWER in answer.lower(), answer
    # No warehouse value from the legacy answer may appear in the rendered text.
    for row in legacy["rows"][:50]:
        assert str(row["sku"]) not in answer, answer
    assert "below reorder level" not in answer.lower()
    assert str(len(legacy["rows"])) not in answer
    assert not re.search(r"SKU-\d", answer), answer


def test_grounded_engine_failure_abstains(engine_raises, legacy_low_stock) -> None:
    out = query_service.answer_question(LOW_STOCK_Q, require_grounding=True)
    _assert_abstain_envelope(out, legacy_low_stock)
    assert not out.get("chart_spec")
    assert not out.get("sources")


def test_grounded_engine_import_failure_abstains(
    monkeypatch: pytest.MonkeyPatch, legacy_low_stock
) -> None:
    # An engine that cannot even import is still an engine failure.
    monkeypatch.setitem(sys.modules, "CortexOS.dms.answer_engine", None)
    out = query_service.answer_question(LOW_STOCK_Q, require_grounding=True)
    _assert_abstain_envelope(out, legacy_low_stock)


def test_manifest_error_still_reraised_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    import CortexOS.dms.answer_engine as ae

    err = ManifestError("refused by manifest")

    def _refuse(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise err

    monkeypatch.setattr(ae, "answer", _refuse)
    for grounded in (True, False):
        with pytest.raises(ManifestError) as info:
            query_service.answer_question(LOW_STOCK_Q, require_grounding=grounded)
        assert info.value is err


def test_ungrounded_engine_failure_still_serves_legacy(
    engine_raises, legacy_low_stock
) -> None:
    """No false positive: ungated in-process callers keep the legacy path."""
    out = query_service.answer_question(LOW_STOCK_Q, require_grounding=False)
    assert out["rows"], out["answer"]
    assert out["sql_used"]
    assert out.get("badge") != "abstain"
    assert "below reorder level" in out["answer"].lower()
    assert str(out["rows"][0]["sku"]) in out["answer"]
    assert len(out["rows"]) == len(legacy_low_stock["rows"])


def test_engine_success_envelope_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    import CortexOS.dms.answer_engine as ae

    served = {
        "answer": "Total revenue is 42.",
        "rows": [{"revenue": 42}],
        "sql_used": "SELECT 42 AS revenue",
        "route": "governed_metric",
        "badge": "governed_metric",
        "audit_id": "a-1",
    }
    monkeypatch.setattr(ae, "answer", lambda *_a, **_kw: served)
    for grounded in (True, False):
        out = query_service.answer_question("total revenue", require_grounding=grounded)
        assert out is served
        assert out["answer"] == "Total revenue is 42."
        assert out["rows"] == [{"revenue": 42}]


def test_delayed_bridge_still_serves_legacy_when_engine_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import CortexOS.dms.answer_engine as ae

    monkeypatch.setattr(
        ae,
        "answer",
        lambda *_a, **_kw: {
            "answer": "Please clarify.",
            "rows": [],
            "route": "needs_clarification",
            "badge": "abstain",
        },
    )
    out = query_service.answer_question(DELAYED_Q, require_grounding=True)
    assert out["route"] == "sql"
    assert out["sql_used"]
    assert out["rows"], out["answer"]
    assert 0 < len(out["rows"]) <= 5
    assert "clarify" not in out["answer"].lower()


def test_legacy_rag_without_sources_stamps_abstain(engine_raises) -> None:
    out = query_service.answer_question(NO_DOC_Q, require_grounding=False)
    assert out["route"] == "rag"
    assert out["sources"] == []
    assert out["rows"] == []
    assert out["badge"] == "abstain"
    assert out["answer"].strip(), "abstain must say so, not render an empty answer"
    assert "could not find a document" in out["answer"].lower()


def test_legacy_rag_with_sources_is_not_abstain(engine_raises) -> None:
    """No false positive: a matched document is still served."""
    out = query_service.answer_question(DOC_Q, require_grounding=False)
    assert out["route"] == "rag"
    assert out["sources"], out
    assert out.get("badge") != "abstain"
    assert "payment terms" in out["answer"].lower()


# ── HTTP: POST /dms/query ─────────────────────────────────────────────────────


@pytest.fixture
def dms_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from packs.dms.security.rate_limit import reset_limiter

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    import netie.config

    netie.config._cached_config = None
    reset_limiter(10_000)
    yield TestClient(create_app())
    reset_limiter()
    netie.config._cached_config = None


def test_http_dms_query_engine_failure_returns_abstain(
    dms_client, engine_raises, legacy_low_stock
) -> None:
    resp = dms_client.post(
        "/dms/query", json={"question": LOW_STOCK_Q, "session_id": "trust02-unbound"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_abstain_envelope(body, legacy_low_stock)
    assert body["route"] == "abstain"
    assert body["layer"] == "abstain"
    assert not body.get("row_count")
    assert body["answer"] == query_service.ENGINE_FAILURE_ANSWER
