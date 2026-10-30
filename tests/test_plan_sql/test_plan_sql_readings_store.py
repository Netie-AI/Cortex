"""PICK-READING-01: the reading store and the contract 1.10.0 models.

The store holds offered readings for a pick: no rows, read by exact id, bound
to the offering ask, never past the manifest's expiry. The contract models
carry the offer (``PlanSqlReconfirm.readings``), the pick
(``AskRequest.plan_sql_reading_id``) and its echo
(``Answer.plan_sql_reading_id``); empty ones are omitted, so a 1.9 envelope
serializes byte for byte as before.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest
from cortex_contract.answer import (
    Answer,
    AskRequest,
    PlanSqlReading,
    PlanSqlReconfirm,
    Provenance,
)
from pydantic import ValidationError

from CortexOS.dms import plan_sql_ask
from CortexOS.plan_sql import readings as plan_readings
from CortexOS.plan_sql.generator import StepStamp
from tests.test_plan_sql.test_c_loop_a_plan_sql import PAYLOAD


@pytest.fixture(autouse=True)
def _clean_store():
    plan_readings.reset_for_tests()
    yield
    plan_readings.reset_for_tests()


def _held(session: str = "s1", *, expires_at: float | None = None) -> plan_readings.HeldReading:
    return plan_readings.HeldReading(
        reading_id=plan_readings.new_reading_id(),
        model_text="SELECT 1",
        served_sql="SELECT 1",
        statement="Returns a",
        row_count=1,
        rung=plan_sql_ask.RUNG_PLAN,
        stamp=StepStamp("sql", "openvault-freeroute", served_provider="p", served_model="m"),
        index=1,
        of=2,
        session_id=session,
        space_id=None,
        manifest_hash="h",
        request_digest="d",
        scored_pack_id=None,
        expires_at=expires_at if expires_at is not None else 4_000_000_000.0,
    )


def _bound(held: plan_readings.HeldReading, **change: object) -> bool:
    binding: dict[str, object] = {
        "session_id": held.session_id,
        "space_id": held.space_id,
        "manifest_hash": held.manifest_hash,
        "request_digest": held.request_digest,
        "scored_pack_id": held.scored_pack_id,
        "now": held.expires_at - 1,
    }
    binding.update(change)
    return plan_sql_ask._reading_bound(held, **binding)  # type: ignore[arg-type]


# -- the store ------------------------------------------------------------------------


def test_a_hold_carries_no_rows() -> None:
    names = {f.name for f in dataclasses.fields(plan_readings.HeldReading)}
    assert not {"rows", "preview", "columns"} & names
    assert {"model_text", "served_sql", "statement", "row_count", "rung", "stamp"} <= names


def test_readings_are_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(plan_readings.READINGS_ENV, raising=False)
    assert plan_readings.readings_enabled() is False
    monkeypatch.setenv(plan_readings.READINGS_ENV, "1")
    assert plan_readings.readings_enabled() is True


def test_global_cap_evicts_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_readings, "MAX_HOLDS", 3)
    store = plan_readings.store()
    held = [_held(f"s{i}") for i in range(4)]
    for item in held:
        store.hold(item)
    assert store.get(held[0].reading_id) is None
    assert all(store.get(item.reading_id) is item for item in held[1:])
    assert len(store) == 3


def test_a_tenth_hold_evicts_only_that_sessions_oldest() -> None:
    store = plan_readings.store()
    other = _held("other")
    store.hold(other)
    mine = [_held("mine") for _ in range(plan_readings.MAX_HOLDS_PER_SESSION + 1)]
    for item in mine:
        store.hold(item)
    assert store.get(mine[0].reading_id) is None
    assert all(store.get(item.reading_id) is item for item in mine[1:])
    assert store.get(other.reading_id) is other


def test_expired_holds_are_swept_on_the_next_hold() -> None:
    store = plan_readings.store()
    old = _held(expires_at=100.0)
    store.hold(old, now=50.0)
    store.hold(_held("s2"), now=200.0)
    assert store.get(old.reading_id) is None


def test_the_hold_never_outlives_the_manifest() -> None:
    now = 1_900_000_000.0
    soon = datetime.fromtimestamp(now + 60, tz=timezone.utc)
    later = datetime.fromtimestamp(now + 7200, tz=timezone.utc)
    assert plan_readings.expiry(soon.isoformat(), now=now) == now + 60
    assert plan_readings.expiry(later.isoformat(), now=now) == now + plan_readings.TTL_SECONDS
    zulu = (datetime.fromtimestamp(now, tz=timezone.utc) + timedelta(seconds=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert plan_readings.expiry(zulu, now=now) == now + 30
    assert plan_readings.expiry("not a time", now=now) == now


def test_get_returns_an_expired_hold_and_the_guard_rejects_it() -> None:
    held = _held(expires_at=1000.0)
    plan_readings.store().hold(held, now=10.0)
    assert plan_readings.store().get(held.reading_id) is held
    assert _bound(held)
    assert not _bound(held, now=1000.0)
    assert not _bound(held, now=5000.0)


@pytest.mark.parametrize(
    "field", ["session_id", "space_id", "manifest_hash", "request_digest", "scored_pack_id"]
)
def test_the_guard_needs_every_binding_field(field: str) -> None:
    assert not _bound(_held(), **{field: "something-else"})


def test_request_digest_covers_question_payload_and_schema_context() -> None:
    base = plan_readings.request_digest("q", PAYLOAD, None)
    assert base == plan_readings.request_digest("q", json.loads(json.dumps(PAYLOAD)), None)
    assert base != plan_readings.request_digest("q2", PAYLOAD, None)
    assert base != plan_readings.request_digest("q", {**PAYLOAD, "joins": [{}]}, None)
    assert base != plan_readings.request_digest("q", PAYLOAD, "SCHEMA")


def test_ids_are_unguessable_and_distinct() -> None:
    ids = {plan_readings.new_reading_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(16 <= len(i) <= 64 for i in ids)


# -- contract 1.10.0 -------------------------------------------------------------------


def _reading(**change: object) -> dict[str, object]:
    reading: dict[str, object] = {
        "reading_id": "r1",
        "statement": "Returns n",
        "row_count": 4,
        "expires_at": "2026-10-30T00:00:00Z",
    }
    reading.update(change)
    return reading


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({}, "reading_needs_payload"),
        ({"dms_payload": PAYLOAD, "plan_sql_confirm": "yes"}, "reading_excludes_confirm"),
        ({"dms_payload": PAYLOAD, "plan_sql_confirm": "no"}, "reading_excludes_confirm"),
    ],
)
def test_the_pick_validator_rejects(extra: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError) as caught:
        AskRequest.model_validate({"question": "q", "plan_sql_reading_id": "r1", **extra})
    assert error in {e["type"] for e in caught.value.errors()}


def test_a_pick_with_a_payload_validates() -> None:
    body = AskRequest.model_validate(
        {"question": "q", "plan_sql_reading_id": "r1", "dms_payload": PAYLOAD}
    )
    assert body.plan_sql_reading_id == "r1"
    assert AskRequest.model_validate({"question": "q"}).plan_sql_reading_id is None


@pytest.mark.parametrize("reading_id", ["", "r" * 65])
def test_the_pick_id_length_is_bounded(reading_id: str) -> None:
    with pytest.raises(ValidationError):
        AskRequest.model_validate(
            {"question": "q", "plan_sql_reading_id": reading_id, "dms_payload": PAYLOAD}
        )


def test_a_reading_is_bounded() -> None:
    PlanSqlReading.model_validate(_reading())
    for bad in (
        {"statement": "x" * 321},
        {"statement": ""},
        {"row_count": 0},
        {"reading_id": "r" * 65},
    ):
        with pytest.raises(ValidationError):
            PlanSqlReading.model_validate(_reading(**bad))
    with pytest.raises(ValidationError):
        PlanSqlReconfirm(why="w", closest_question="c", readings=[_reading()] * 4)


def test_empty_readings_and_a_null_echo_are_omitted() -> None:
    plain = PlanSqlReconfirm(why="w", closest_question="c")
    assert plain.model_dump() == {"why": "w", "closest_question": "c"}
    assert json.loads(plain.model_dump_json()) == {"why": "w", "closest_question": "c"}
    offered = PlanSqlReconfirm(why="w", closest_question="c", readings=[_reading()])
    assert offered.model_dump()["readings"] == [_reading()]
    answer = Answer(
        answer="a",
        audit_id="x",
        route="plan_sql",
        provenance=Provenance(layer="plan_sql", badge="session"),
    )
    assert "plan_sql_reading_id" not in answer.model_dump()
    assert "plan_sql_reading_id" not in json.loads(answer.model_dump_json())
    echoed = answer.model_copy(update={"plan_sql_reading_id": "r1"})
    assert echoed.model_dump()["plan_sql_reading_id"] == "r1"
    # Round trip through the dump the contract route uses.
    assert Answer.model_validate(answer.model_dump()) == answer


def test_the_published_schema_carries_the_new_fields() -> None:
    reconfirm = PlanSqlReconfirm.model_json_schema(mode="serialization")
    assert "readings" in reconfirm["properties"]
    assert "readings" not in reconfirm.get("required", [])
    answer = Answer.model_json_schema(mode="serialization")
    assert "plan_sql_reading_id" in answer["properties"]
    assert "plan_sql_reading_id" not in answer.get("required", [])
