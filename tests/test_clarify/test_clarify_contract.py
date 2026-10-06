"""CLARIFY (#307) contract 1.5.0: the clarify variant of the answer envelope.

Must-fail (2): a clarify without 2-5 options or without a named ambiguity type
is refused by the contract model DMS pins. A clarify answer never carries SQL,
rows, a row count, a drillthrough token or a confident badge. An answer with
no clarify serialises exactly the 1.4.0 keys, so a 1.4 envelope is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import (
    AmbiguityType,
    Answer,
    Badge,
    Clarify,
    ClarifyOption,
    Provenance,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def _option(n: int) -> dict[str, Any]:
    return {
        "id": f"opt_{n}",
        "label": f"Within {n} days",
        "resolved_question": f"revenue within {n} days",
        "interpretation": {"metric_id": "revenue_windowed", "days": str(n)},
    }


def _clarify(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "question": "Which time window do you mean by 'recent'?",
        "ambiguity_type": "time_window",
        "term": "recent",
        "options": [_option(7), _option(30)],
        "served_by": "cortex:clarify",
        "served_at": "2026-10-06T00:00:00+00:00",
        "served_reason": "'recent' names no window",
        "served_catalog": "sha256:test",
    }
    body.update(overrides)
    return body


def _answer(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "answer": "Which time window do you mean by 'recent'?",
        "audit_id": "a-1",
        "route": "clarify",
        "provenance": {"layer": "clarify", "badge": "abstain"},
        "clarify": _clarify(),
    }
    body.update(overrides)
    return body


def test_clarify_with_options_and_type_validates() -> None:
    clarify = Clarify.model_validate(_clarify())
    assert clarify.ambiguity_type is AmbiguityType.TIME_WINDOW
    assert [o.id for o in clarify.options] == ["opt_7", "opt_30"]
    answer = Answer.model_validate(_answer())
    assert answer.clarify == clarify
    assert answer.provenance.badge is Badge.ABSTAIN


@pytest.mark.parametrize("count", [0, 1, 6])
def test_must_fail_clarify_without_two_to_five_options_is_refused(count: int) -> None:
    with pytest.raises(ValidationError, match="2-5 options"):
        Clarify.model_validate(_clarify(options=[_option(n + 1) for n in range(count)]))


def test_must_fail_clarify_without_options_field_is_refused() -> None:
    body = _clarify()
    del body["options"]
    with pytest.raises(ValidationError):
        Clarify.model_validate(body)


def test_must_fail_clarify_with_duplicate_option_ids_is_refused() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Clarify.model_validate(_clarify(options=[_option(7), _option(7)]))


@pytest.mark.parametrize("value", ["", None, "grain", "abstain"])
def test_must_fail_clarify_with_no_named_ambiguity_type_is_refused(value: Any) -> None:
    with pytest.raises(ValidationError):
        Clarify.model_validate(_clarify(ambiguity_type=value))


def test_must_fail_clarify_missing_ambiguity_type_is_refused() -> None:
    body = _clarify()
    del body["ambiguity_type"]
    with pytest.raises(ValidationError):
        Clarify.model_validate(body)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sql_used": "SELECT 1"},
        {"rows": [{"revenue_myr": 1.0}]},
        {"row_count": 1},
        {"drillthrough_token": "tok"},
        {"provenance": {"layer": "clarify", "badge": "session"}},
        {"provenance": {"layer": "clarify", "badge": "governed_metric"}},
    ],
)
def test_must_fail_clarify_answer_never_carries_a_number(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="clarify answer"):
        Answer.model_validate(_answer(**overrides))


def _frozen_answer_keys(version: str) -> set[str]:
    spec = json.loads((ROOT / "contract" / f"openapi-{version}.json").read_text(encoding="utf-8"))
    return set(spec["components"]["schemas"]["ContractAnswer"]["properties"])


def test_answer_without_clarify_serialises_exactly_the_1_4_keys() -> None:
    answer = Answer(
        answer="Result: 1",
        audit_id="a-1",
        route="governed_metric",
        provenance=Provenance(layer="governed_metric", badge=Badge.GOVERNED_METRIC),
    )
    keys = set(answer.model_dump().keys())
    assert keys == _frozen_answer_keys("1.4.0")
    assert set(json.loads(answer.model_dump_json())) == keys
    assert "clarify" not in keys and "clarify_resolved" not in keys


def test_clarify_answer_puts_the_variant_on_the_wire() -> None:
    answer = Answer.model_validate(_answer(clarify_resolved=["opt_prev"]))
    wire = json.loads(answer.model_dump_json())
    assert wire["clarify"]["ambiguity_type"] == "time_window"
    assert wire["clarify_resolved"] == ["opt_prev"]
    assert set(wire) == _frozen_answer_keys("1.4.0") | {"clarify", "clarify_resolved"}


def test_spec_1_5_publishes_the_clarify_variant() -> None:
    spec = json.loads((ROOT / "contract" / "openapi-1.5.0.json").read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    assert set(schemas["ContractAnswer"]["properties"]) == _frozen_answer_keys("1.4.0") | {
        "clarify",
        "clarify_resolved",
    }
    clarify = schemas["ContractClarify"]
    assert clarify["properties"]["options"]["minItems"] == 2
    assert clarify["properties"]["options"]["maxItems"] == 5
    assert "ambiguity_type" in clarify["required"] and "options" in clarify["required"]
    assert set(schemas["ContractAmbiguityType"]["enum"]) == {t.value for t in AmbiguityType}
    assert "clarify_option_ids" in schemas["ContractAskRequest"]["properties"]


def test_option_model_requires_a_resolvable_question() -> None:
    with pytest.raises(ValidationError):
        ClarifyOption.model_validate({**_option(7), "resolved_question": ""})
