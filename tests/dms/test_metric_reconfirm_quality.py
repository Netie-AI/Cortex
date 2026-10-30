"""The governed-metric reconfirm offers a question Yes can run, and No logs a ticket.

An armed held-out run after #424 found three gaps on the metric path:

1. A ``none`` from the chooser with no closest question reconfirmed with the
   placeholder "a declared question"; Yes on it only reconfirmed again. Other
   suggestions were fragments or showed an internal metric id. The closest
   question is now asked back through the same choose and check before it is
   offered; one that is not served, or that shows the id, is replaced by an
   honest fixed text, and Yes on that text answers not found.
2. No on the metric reconfirm answered "not found in the database" and logged
   no pipeline-failure ticket. It now logs the same F1 ticket as the plan+SQL
   path: identifiers only, never the question text.
3. The legacy bridge answered a document miss with a blank, unbadged envelope.
   A miss now falls through to the ladder.

Every answer-path assertion is on the customer envelope of
``POST /v1/contract/ask`` or ``POST /dms/query``: badge, answer text, rows.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

import pytest

from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

SESSION = "reconfirm-quality"
GRANT = {
    "inventory": "TRUE",
    "suppliers": "TRUE",
    "locations": "TRUE",
    "shipments": "TRUE",
    "transactions": "TRUE",
    "alerts": "TRUE",
}
ASK = "/v1/contract/ask"
QUERY = "/dms/query"
DELAYED_COUNT = 1031
NOT_DELAYED_COUNT = 10969

NOT_DELAYED = "How many shipments are not delayed?"
ANSWERABLE = "How many shipments are delayed?"
FRAGMENT_ASK = "Which shipments are partly delayed?"
FRAGMENT = "partly delayed shipments with"
ID_ASK = "How many shipments are delayed by carrier?"
SHOWS_ID = "What is delayed_count for all shipments?"
REJECTED_ASK = "How many shipments are delayed less than a day?"
WHY = "The declared metrics count delayed shipments, not shipments that are not delayed"
TICKET_EVENT = "plan_sql.pipeline_failure"


class _Port:
    """A recorded chooser. Only ANSWERABLE and SHOWS_ID serve delayed_count."""

    def __init__(self) -> None:
        self.chose: list[str] = []

    def choose(self, question: str) -> Any:
        from CortexOS.dms.metric_choice import MetricSelection

        self.chose.append(question)
        if question in (ANSWERABLE, SHOWS_ID, REJECTED_ASK):
            return MetricSelection(metric_id="delayed_count", why="delayed shipments")
        closest = {NOT_DELAYED: ANSWERABLE, FRAGMENT_ASK: FRAGMENT, ID_ASK: SHOWS_ID}
        return MetricSelection(
            closest_question=closest.get(question, ""),
            why=f"{WHY}, so it cannot be served directly",
        )

    def check(self, question: str, selection: Any) -> Any:
        from CortexOS.dms.metric_choice import MetricVerdict

        del selection
        if question in (ANSWERABLE, SHOWS_ID):
            return MetricVerdict(agrees=True, unsure=False, why="")
        return MetricVerdict(agrees=False, unsure=False, why="")


@pytest.fixture
def port() -> Any:
    from CortexOS.dms.metric_choice import clear_metric_choice, register_metric_choice

    recorded = _Port()
    register_metric_choice(recorded)
    yield recorded
    clear_metric_choice()


@pytest.fixture
def ticket_ledger(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The DMS pack's F1 ledger on a per-test DMS_OPS_DB, on the engine seam."""
    from CortexOS.audit import ledger_registry
    from packs.dms.audit import ledger

    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "dms_ops.db"))

    monkeypatch.setattr(ledger_registry, "_ledger", ledger)
    return ledger


def _tickets(ledger: Any) -> list[Any]:
    return list(ledger.list_entries(event_type=TICKET_EVENT))


def _post(client: Any, path: str, question: str, confirm: str | None = None) -> dict[str, Any]:
    client.bind_session(SESSION, GRANT)
    payload: dict[str, Any] = {"question": question, "session_id": SESSION}
    if confirm is not None:
        payload["plan_sql_confirm"] = confirm
    resp = client.post(path, json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def _badge(body: dict[str, Any]) -> str:
    raw = body.get("badge")
    if raw:
        return str(raw).lower()
    prov = body.get("provenance") or {}
    return str(prov.get("badge") or "").lower() if isinstance(prov, dict) else ""


def _closest(body: dict[str, Any]) -> str:
    return str((body.get("reconfirm") or {}).get("closest_question") or "")


def _assert_reconfirm(body: dict[str, Any]) -> str:
    assert _badge(body) == "reconfirm", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert not body.get("sql_used"), body.get("sql_used")
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert "Yes runs that question" in text
    assert "not found in the database" in text
    plain = text.replace(",", "")
    assert str(DELAYED_COUNT) not in plain and str(NOT_DELAYED_COUNT) not in plain
    # Internal ids never reach the customer text.
    assert "delayed_count" not in text, text
    assert "a declared question" not in text, text
    return text


def _assert_not_found(body: dict[str, Any]) -> None:
    assert body.get("answer") == "not found in the database", body
    assert _badge(body) == "abstain", body
    assert body.get("rows") in ([], None)
    assert not body.get("sql_used")
    assert body.get("reconfirm") is None


@pytest.mark.parametrize("path", [ASK, QUERY])
def test_must_fail_reconfirm_offers_a_closest_question_yes_can_run(
    dms_http: Any, port: _Port, path: str  # noqa: F811
) -> None:
    """The model's suggestion is asked back and served, so it is offered."""
    body = _post(dms_http, path, NOT_DELAYED)
    text = _assert_reconfirm(body)
    assert f"Closest question: {ANSWERABLE}" in text, text
    assert WHY in text, text
    if path == ASK:
        assert _closest(body) == ANSWERABLE
        assert body.get("suggestions") == [ANSWERABLE]
    # Asked back through the same choose and check before it was offered.
    assert ANSWERABLE in port.chose

    yes = _post(dms_http, ASK, _closest(body) or ANSWERABLE, confirm="yes")
    assert _badge(yes) == "governed_metric", yes
    assert yes.get("reconfirm") is None
    rows = yes.get("rows") or []
    assert rows and int(rows[0]["delayed_count"]) == DELAYED_COUNT, rows
    assert str(DELAYED_COUNT) in str(yes.get("answer") or "").replace(",", "")


@pytest.mark.parametrize(
    ("question", "offered"),
    [(FRAGMENT_ASK, FRAGMENT), (ID_ASK, SHOWS_ID)],
    ids=["fragment-is-not-served", "suggestion-shows-the-metric-id"],
)
def test_must_fail_unanswerable_suggestion_is_not_offered(
    dms_http: Any, port: _Port, question: str, offered: str  # noqa: F811
) -> None:
    """A fragment does not serve, and a suggestion that shows the id is not shown."""
    from CortexOS.dms.metric_choice import NO_CLOSEST

    body = _post(dms_http, ASK, question)
    text = _assert_reconfirm(body)
    assert offered not in text, text
    assert "Closest question: none." in text, text
    assert NO_CLOSEST in text
    assert _closest(body) == NO_CLOSEST


def test_must_fail_check_rejection_why_does_not_show_the_metric_id(
    dms_http: Any, port: _Port  # noqa: F811
) -> None:
    """A metric the check rejects reconfirms without naming the metric's id."""
    body = _post(dms_http, ASK, REJECTED_ASK)
    text = _assert_reconfirm(body)
    assert "does not mean the same thing as the question" in text, text
    assert "delayed_count" not in str((body.get("reconfirm") or {}).get("why"))


def test_must_fail_metric_confirm_no_logs_a_pipeline_failure_ticket(
    dms_http: Any, port: _Port, ticket_ledger: Any  # noqa: F811
) -> None:
    """CLAUDE.md sections 8 and 9: No answers not found and logs a ticket.

    Identifiers only: the question is a SHA-256 and a length, never text.
    """
    assert _tickets(ticket_ledger) == []
    body = _post(dms_http, ASK, NOT_DELAYED, confirm="no")
    _assert_not_found(body)
    tickets = _tickets(ticket_ledger)
    assert len(tickets) == 1, tickets
    ticket = tickets[0]
    assert ticket.actor == "cortex.plan_sql"
    assert ticket.payload == {
        "audit_id": body["audit_id"],
        "question_sha256": hashlib.sha256(NOT_DELAYED.encode("utf-8")).hexdigest(),
        "question_chars": len(NOT_DELAYED),
        "reason": "user declined the closest question",
        "session_id": SESSION,
        "space_id": None,
        "tables": [],
    }
    entry_text = json.dumps(dataclasses.asdict(ticket), sort_keys=True, default=str)
    assert NOT_DELAYED.lower() not in entry_text.lower()
    step = next(line for line in body["assumptions"] if line.startswith("step ticket:"))
    assert step.endswith(f"({TICKET_EVENT} recorded)"), step
    assert "seq" not in step
    assert ticket_ledger.verify().ok


def test_must_fail_yes_on_no_closest_question_answers_not_found_with_a_ticket(
    dms_http: Any, port: _Port, ticket_ledger: Any  # noqa: F811
) -> None:
    """Yes on the honest no-closest text has nothing to run. It does not reconfirm."""
    from CortexOS.dms.metric_choice import NO_CLOSEST

    first = _post(dms_http, ASK, FRAGMENT_ASK)
    assert _closest(first) == NO_CLOSEST
    body = _post(dms_http, ASK, _closest(first), confirm="yes")
    _assert_not_found(body)
    tickets = _tickets(ticket_ledger)
    assert len(tickets) == 1, tickets
    assert tickets[0].payload["reason"] == "no closest question could be confirmed answerable"


def test_served_metric_logs_no_ticket(
    dms_http: Any, port: _Port, ticket_ledger: Any  # noqa: F811
) -> None:
    """No on a question the check accepts is a new ask: rows, no ticket."""
    body = _post(dms_http, ASK, ANSWERABLE, confirm="no")
    assert _badge(body) == "governed_metric", body
    assert int((body.get("rows") or [{}])[0]["delayed_count"]) == DELAYED_COUNT
    assert _tickets(ticket_ledger) == []


def test_must_fail_legacy_document_miss_is_not_a_blank_envelope(
    port: _Port, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document miss on the legacy bridge falls through to the ladder.

    The bridge is not mounted on /dms/query (that route reaches the engine),
    so it is driven directly. The envelope must not be the blank
    ``{answer: '', route: 'rag', sources: []}``.
    """
    from CortexOS.dms import query_service

    monkeypatch.setattr(query_service, "route_question", lambda q: "rag")
    monkeypatch.setattr(query_service, "rag_answer", lambda q: ("", []))
    body = query_service._answer_question_legacy(NOT_DELAYED)
    assert str(body.get("answer") or "").strip(), body
    assert body.get("route") != "rag", body
    assert body.get("badge") == "reconfirm", body
    assert body.get("rows") in ([], None)
    assert not body.get("sql_used")
    assert f"Closest question: {ANSWERABLE}" in str(body.get("answer"))


def test_legacy_document_hit_is_still_served(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.dms import query_service

    monkeypatch.setattr(query_service, "route_question", lambda q: "rag")
    monkeypatch.setattr(query_service, "rag_answer", lambda q: ("Net 30 [1]", ["a.txt#1"]))
    body = query_service._answer_question_legacy("What are the payment terms?")
    assert body["answer"] == "Net 30 [1]"
    assert body["route"] == "rag" and body["sources"] == ["a.txt#1"]


class _FakeFreeRoute:
    def __init__(self, choose_body: dict[str, Any]) -> None:
        self.choose_body = choose_body

    def complete(self, task: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from CortexOS.dms.metric_choice import CHOOSE_TASK
        from CortexOS.integrations.freeroute import Completion

        del messages, kwargs
        if task == CHOOSE_TASK:
            return Completion(ok=True, text=json.dumps(self.choose_body))
        return Completion(ok=False, reason=f"scripted refusal for {task}")


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> Any:
    from CortexOS.integrations import freeroute
    from packs.dms.metric_choice import OpenVaultMetricChoice

    armed = freeroute.Arming(armed=True, reason="scripted", url="http://scripted")
    monkeypatch.setattr(freeroute, "arming", lambda *a, **k: armed)
    fake = _FakeFreeRoute({})
    monkeypatch.setattr(freeroute, "complete", fake.complete)
    return fake, OpenVaultMetricChoice()


def test_must_fail_adapter_keeps_ids_out_of_why_and_closest(adapter: Any) -> None:
    """The model's why reaches the customer; one that shows an id is not used."""
    from CortexOS.dms.metric_choice import NO_CLOSEST

    fake, chooser = adapter
    fake.choose_body = {
        "metric_id": "none",
        "slots": {},
        "why": "delayed_count counts only delayed shipments",
        "closest_question": "What is delayed_count?",
    }
    selection = chooser.choose(NOT_DELAYED)
    assert "delayed_count" not in selection.why, selection.why
    assert selection.closest_question == NO_CLOSEST

    fake.choose_body = {
        "metric_id": "none",
        "slots": {},
        "why": WHY,
        "closest_question": f"  {ANSWERABLE}\n",
    }
    selection = chooser.choose(NOT_DELAYED)
    assert selection.why == f"{WHY}, so it cannot be served directly"
    assert selection.closest_question == ANSWERABLE


def test_must_fail_adapter_loose_value_why_shows_no_slot_name(adapter: Any) -> None:
    fake, chooser = adapter
    fake.choose_body = {
        "metric_id": "shipments_by_status",
        "slots": {"status": "NOT_DELAYED"},
        "closest_question": ANSWERABLE,
    }
    from packs.dms.semantic.loader import load_all

    params = load_all().metrics["shipments_by_status"].params
    selection = chooser.choose(NOT_DELAYED)
    assert selection.metric_id == "none"
    assert "NOT_DELAYED" not in selection.why
    for name in params:
        assert f"{name}=" not in selection.why, selection.why
