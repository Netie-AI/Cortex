"""#412: the keyword cascade serves only a metric's declared question.

On main, "how many shipments are not delayed" took the L1 keyword cascade to
``delayed_count`` (1031). The warehouse has 10969 shipments that are not
delayed. A question that is not the declared question reconfirms: why it
cannot be served directly, the closest declared question, Yes runs that
question, No answers "not found in the database". With the model step on, the
question goes to that step instead. The guard has no token list.

Must-fails (each goes red with the guard removed; see
test_metric_exactness_guard_mutations.py):

* ``test_must_fail_near_miss_is_not_served_as_delayed_count`` -- seven
  phrasings, ``POST /dms/query`` and ``POST /v1/contract/ask``.
* ``test_must_fail_near_miss_reaches_the_model_step`` -- the same question is
  handed to the model step, and the declared delayed question is not.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

SESSION = "exact-412"
GRANT = {
    "inventory": "TRUE",
    "suppliers": "TRUE",
    "locations": "TRUE",
    "shipments": "TRUE",
    "transactions": "TRUE",
    "alerts": "TRUE",
}
DELAYED_COUNT = 1031
NOT_DELAYED_COUNT = 10969
NEAR_MISSES = [
    "How many shipments are not delayed?",
    "What is the number of shipments that are not delayed?",
    "How many shipments were never delayed?",
    "How many shipments aren't delayed?",
    "How many shipments are without delayed status?",
    "How many shipments except delayed ones?",
    "How many shipments are undelayed?",
]
DECLARED = "how many delayed"


def _ask(client: Any, path: str, question: str) -> dict[str, Any]:
    client.bind_session(SESSION, GRANT)
    resp = client.post(path, json={"question": question, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def _badge(body: dict[str, Any]) -> str:
    raw = body.get("badge")
    if raw:
        return str(raw)
    prov = body.get("provenance") or {}
    if isinstance(prov, dict):
        return str(prov.get("badge") or "")
    return ""


def _assert_not_the_delayed_count(body: dict[str, Any]) -> None:
    assert _badge(body).lower() == "reconfirm", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert not body.get("sql_used"), body.get("sql_used")
    assert body.get("metric_id") in (None, ""), body.get("metric_id")
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert "Closest question:" in text
    assert "Yes runs that question" in text
    assert "not found in the database" in text
    assert "1031" not in text and "1,031" not in text
    assert "10969" not in text and "10,969" not in text
    recon = body.get("reconfirm") or {}
    if recon:
        assert recon.get("closest_question")
        assert recon.get("why")


def test_warehouse_counts_are_the_ones_this_bug_mixes_up() -> None:
    """The rule's number and the complement, read from the demo warehouse."""
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms.warehouse_db import DEFAULT_DB

    _ensure_db_loaded()
    con = duckdb.connect(str(DEFAULT_DB), read_only=True)
    try:
        delayed = con.execute(
            "SELECT COUNT(*) FROM shipments WHERE status = 'DELAYED'"
        ).fetchone()
        other = con.execute(
            "SELECT COUNT(*) FROM shipments WHERE status <> 'DELAYED'"
        ).fetchone()
    finally:
        con.close()
    assert delayed is not None and int(delayed[0]) == DELAYED_COUNT
    assert other is not None and int(other[0]) == NOT_DELAYED_COUNT


@pytest.mark.parametrize("question", NEAR_MISSES)
def test_must_fail_near_miss_is_not_served_as_delayed_count(
    dms_http: Any, question: str  # noqa: F811
) -> None:
    for path in ("/dms/query", "/v1/contract/ask"):
        _assert_not_the_delayed_count(_ask(dms_http, path, question))


def test_declared_delayed_question_stays_on_the_rule(dms_http: Any) -> None:  # noqa: F811
    body = _ask(dms_http, "/dms/query", DECLARED)
    assert body.get("layer") == "governed_metric", body
    assert body.get("metric_id") == "delayed_count"
    rows = body.get("rows") or []
    assert rows and int(rows[0]["delayed_count"]) == DELAYED_COUNT
    assert "1031" in str(body.get("answer") or "").replace(",", "")


def test_must_fail_no_answers_not_found(dms_http: Any) -> None:  # noqa: F811
    dms_http.bind_session(SESSION, GRANT)
    resp = dms_http.post(
        "/v1/contract/ask",
        json={
            "question": NEAR_MISSES[0],
            "session_id": SESSION,
            "plan_sql_confirm": "no",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("answer") == "not found in the database"
    assert body.get("rows") in ([], None)
    assert not body.get("sql_used")
    assert "1031" not in str(body.get("answer"))


def test_gate_contains_no_token_list() -> None:
    """The serve gate matches a declared question. It does not list words."""
    from pathlib import Path

    src = Path("CortexOS/dms/answer_engine.py").read_text(encoding="utf-8")
    start = src.index("def _exactness_guard_enabled")
    end = src.index("def _true_count")
    gate = src[start:end]
    assert "INVARIANT_TOKENS" not in gate
    assert "frozenset" not in gate
    for word in ("aren't", "undelayed", "without", "except", "never"):
        assert word not in gate


class _RecordingPort:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def is_configured(self) -> bool:
        return True

    def retrieve_schema(self, question: str) -> dict[str, Any]:
        del question
        return {"tables": {"shipments": {"columns": ["status"]}}}

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]:
        del schema, prior_violations
        self.questions.append(question)
        return []

    def record_validated(self, question: str, sql: str) -> None:
        del question, sql
        raise AssertionError("a near-miss must not be recorded as validated")


def test_must_fail_near_miss_reaches_the_model_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flags off, the near-miss used to stop at the rule. It now reaches L2.

    The port returns no SQL, so the envelope still abstains. The exact delayed
    count never reaches the port.
    """
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms import l2_generation
    from CortexOS.dms.answer_engine import answer

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    port = _RecordingPort()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)

    near = answer(NEAR_MISSES[0])
    assert port.questions == [NEAR_MISSES[0]]
    assert near.get("badge") == "abstain", near
    assert near.get("rows") in ([], None)
    assert "1031" not in str(near.get("answer") or "")

    port.questions.clear()
    exact = answer(DECLARED)
    assert port.questions == []
    assert exact.get("layer") == "governed_metric"
    assert int(exact["rows"][0]["delayed_count"]) == DELAYED_COUNT
