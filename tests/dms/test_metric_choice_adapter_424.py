"""#424: the real OpenVault metric-choice adapter, driven through FreeRoute.

The #412 tests install a recorded port. These install the production
``OpenVaultMetricChoice`` and fake only ``freeroute.complete`` (and arming), so
the adapter's own JSON reading decides what is served. Every assertion is on
the customer envelope of ``POST /v1/contract/ask``: badge, answer text, rows.

1. The check reply must be the JSON booleans ``agrees: true`` and
   ``unsure: false`` to serve. A string ``"false"``, ``"no"`` or ``"true"``, a
   number, or a missing ``unsure`` is not an agreement. Read as truthy, any of
   them served ``delayed_count`` (1031) for "How many shipments are not
   delayed?", the #412 WRONG.
2. A served metric's envelope does not carry the chooser's refusal prose.
3. A slot value the model writes must be a stored value of its column.
   Containment and fuzzy matching resolved ``NOT_DELAYED``, ``UNDELAYED`` and
   ``NOT_IN_TRANSIT`` back to the positive value, so with an agreeing check
   "not delayed" served the delayed shipments.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

SESSION = "adapter-424"
GRANT = {
    "inventory": "TRUE",
    "suppliers": "TRUE",
    "locations": "TRUE",
    "shipments": "TRUE",
    "transactions": "TRUE",
    "alerts": "TRUE",
}
NOT_DELAYED = "How many shipments are not delayed?"
DECLARED = "how many delayed"
DELAYED_COUNT = 1031
NOT_DELAYED_COUNT = 10969
ASK = "/v1/contract/ask"

# Check replies that are not the JSON booleans true / false.
NOT_AN_AGREEMENT = {
    "agrees_string_false": {"agrees": "false", "unsure": False},
    "agrees_string_no": {"agrees": "no", "unsure": False},
    "agrees_string_true": {"agrees": "true", "unsure": False},
    "agrees_int_one": {"agrees": 1, "unsure": False},
    "unsure_missing": {"agrees": True},
}
# Also not an agreement; 5e4ff2b already refused it (bool("false") is True).
STRING_UNSURE = {"agrees": True, "unsure": "false"}
AGREES = {"agrees": True, "unsure": False}

REFUSAL_PROSE = (
    "did not accept",
    "cannot be served",
    "not armed",
    "nothing usable",
    "does not agree",
)


class _FakeFreeRoute:
    """Scripted replies per FreeRoute task. Any other task is refused."""

    def __init__(self, check_body: dict[str, Any]) -> None:
        self.check_body = check_body
        self.choose_body: dict[str, Any] = {
            "metric_id": "delayed_count",
            "slots": {},
            "closest_question": DECLARED,
        }
        self.tasks: list[str] = []

    def complete(self, task: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from CortexOS.dms.metric_choice import CHECK_TASK, CHOOSE_TASK
        from CortexOS.integrations.freeroute import Completion

        del messages, kwargs
        self.tasks.append(task)
        if task == CHOOSE_TASK:
            return Completion(ok=True, text=json.dumps(self.choose_body))
        if task == CHECK_TASK:
            return Completion(ok=True, text=json.dumps(self.check_body))
        return Completion(ok=False, reason=f"scripted refusal for {task}")


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install the production adapter; FreeRoute answers from a script."""
    from CortexOS.dms.metric_choice import clear_metric_choice, register_metric_choice
    from CortexOS.integrations import freeroute
    from packs.dms.metric_choice import OpenVaultMetricChoice

    armed = freeroute.Arming(armed=True, reason="scripted", url="http://scripted")
    monkeypatch.setattr(freeroute, "arming", lambda *a, **k: armed)
    fake = _FakeFreeRoute(AGREES)
    monkeypatch.setattr(freeroute, "complete", fake.complete)
    register_metric_choice(OpenVaultMetricChoice())
    yield fake
    clear_metric_choice()


def _ask(client: Any, question: str) -> dict[str, Any]:
    client.bind_session(SESSION, GRANT)
    resp = client.post(ASK, json={"question": question, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def _stamp(body: dict[str, Any], key: str) -> str:
    raw = body.get(key)
    if raw:
        return str(raw)
    prov = body.get("provenance") or {}
    if isinstance(prov, dict):
        return str(prov.get(key) or "")
    return ""


def _badge(body: dict[str, Any]) -> str:
    return _stamp(body, "badge")


def _assumptions(body: dict[str, Any]) -> str:
    """Top-level assumptions (a list on the contract) plus the provenance copy."""
    raw = body.get("assumptions") or []
    items = [raw] if isinstance(raw, str) else [str(x) for x in raw]
    prov = body.get("provenance") or {}
    if isinstance(prov, dict) and prov.get("assumptions"):
        items.append(str(prov["assumptions"]))
    return " | ".join(items)


def _number_free(text: str) -> str:
    return text.replace(",", "")


@pytest.mark.parametrize("check_body", NOT_AN_AGREEMENT.values(), ids=NOT_AN_AGREEMENT.keys())
def test_must_fail_non_boolean_check_is_not_served(
    dms_http: Any, adapter: Any, check_body: dict[str, Any]  # noqa: F811
) -> None:
    adapter.check_body = check_body
    body = _ask(dms_http, NOT_DELAYED)
    from CortexOS.dms.metric_choice import CHECK_TASK, CHOOSE_TASK

    # The real adapter was asked both questions through FreeRoute.
    assert CHOOSE_TASK in adapter.tasks and CHECK_TASK in adapter.tasks, adapter.tasks
    assert _badge(body).lower() == "reconfirm", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert not body.get("sql_used"), body.get("sql_used")
    assert _stamp(body, "metric_id") == "", body
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert "Closest question:" in text
    assert str(DELAYED_COUNT) not in _number_free(text), text
    assert str(NOT_DELAYED_COUNT) not in _number_free(text), text


def test_string_unsure_is_not_served(dms_http: Any, adapter: Any) -> None:  # noqa: F811
    adapter.check_body = STRING_UNSURE
    body = _ask(dms_http, NOT_DELAYED)
    assert _badge(body).lower() == "reconfirm", body
    assert body.get("rows") in ([], None), body.get("rows")
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert str(DELAYED_COUNT) not in _number_free(text), text


def test_boolean_agreement_serves_the_declared_metric(
    dms_http: Any, adapter: Any  # noqa: F811
) -> None:
    """Control: the JSON booleans true / false serve through the real adapter."""
    adapter.check_body = AGREES
    body = _ask(dms_http, DECLARED)
    assert _badge(body).lower() == "governed_metric", body
    assert _stamp(body, "metric_id") == "delayed_count", body
    rows = body.get("rows") or []
    assert rows and int(rows[0]["delayed_count"]) == DELAYED_COUNT, rows
    assert str(DELAYED_COUNT) in _number_free(str(body.get("answer") or "")), body


def test_must_fail_served_answer_carries_no_refusal_prose(
    dms_http: Any, adapter: Any  # noqa: F811
) -> None:
    """A green governed_metric badge never carries the chooser's refusal why."""
    adapter.check_body = AGREES
    body = _ask(dms_http, DECLARED)
    assert _badge(body).lower() == "governed_metric", body
    rows = body.get("rows") or []
    assert rows and int(rows[0]["delayed_count"]) == DELAYED_COUNT, rows
    text = str(body.get("answer") or "")
    assert str(DELAYED_COUNT) in _number_free(text), text
    assert _stamp(body, "metric_id") == "delayed_count", body
    assumptions = _assumptions(body)
    envelope = json.dumps(body).lower()
    for phrase in REFUSAL_PROSE:
        assert phrase not in assumptions.lower(), assumptions
        assert phrase not in envelope, (phrase, body)
    # What was served is stated: the metric and that the check agreed.
    assert "delayed_count" in assumptions, assumptions
    assert "agreed" in assumptions.lower(), assumptions


# Slot values a model wrote for "not delayed" / "not in transit" that are not
# stored statuses. Each one contains, or is close to, a stored value.
NEGATED_SLOTS = {
    "not_delayed": "NOT_DELAYED",
    "undelayed": "UNDELAYED",
    "non_delayed": "non-delayed",
    "not_in_transit": "NOT_IN_TRANSIT",
}


@pytest.mark.parametrize("status", NEGATED_SLOTS.values(), ids=NEGATED_SLOTS.keys())
def test_must_fail_a_slot_that_is_not_a_stored_value_is_not_served(
    dms_http: Any, adapter: Any, status: str  # noqa: F811
) -> None:
    adapter.check_body = AGREES
    adapter.choose_body = {
        "metric_id": "shipments_by_status",
        "slots": {"status": status},
        "closest_question": "list shipments that are delayed",
    }
    body = _ask(dms_http, NOT_DELAYED)
    from CortexOS.dms.metric_choice import CHOOSE_TASK

    assert CHOOSE_TASK in adapter.tasks, adapter.tasks
    assert _badge(body).lower() == "reconfirm", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert not body.get("sql_used"), body.get("sql_used")
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert str(DELAYED_COUNT) not in _number_free(text), text
    assert "DELAYED'" not in json.dumps(body), body


def test_a_stored_slot_value_still_serves(dms_http: Any, adapter: Any) -> None:  # noqa: F811
    """Control: a stored status, in any case or spacing, serves the listing."""
    adapter.check_body = AGREES
    adapter.choose_body = {
        "metric_id": "shipments_by_status",
        "slots": {"status": "delayed"},
        "closest_question": "list shipments that are delayed",
    }
    body = _ask(dms_http, "list shipments that are delayed")
    assert _badge(body).lower() == "governed_metric", body
    assert _stamp(body, "metric_id") == "shipments_by_status", body
    assert body.get("rows"), body
    assert str(DELAYED_COUNT) in _number_free(str(body.get("answer") or "")), body
