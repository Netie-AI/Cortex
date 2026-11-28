"""FDE-CORTEX-SPACE-CATALOG-01: the chooser reads the asking Space only.

Two synthetic Spaces with disjoint schemas. A question on Space B must not
see Space A's catalog, or the demo supply-chain catalog, in the prompt, the
reason, or the suggestions. Flag off, a manifest that carries a Space catalog
answers the same as a manifest that does not.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cortex_contract.execution import (
    Manifest,
    SpaceCatalog,
    SpaceDefinition,
    SpaceLink,
    SpaceMeasure,
    SpaceObject,
    canonical_manifest_bytes,
)

from CortexOS.execution.manifest import VerifiedManifest

QUESTION_B = "How many clinic visits were recorded?"
DEMO_NAMES = (
    "inventory",
    "suppliers",
    "shipments",
    "warehouse",
    "transactions",
    "alerts",
    "expired",
)
SPACE_A_NAMES = ("rx_fill", "rx_counter", "fill_count", "prescription")
SPACE_B_NAMES = ("clinic_visit", "clinic_room", "visit_count")


def _catalog_a() -> SpaceCatalog:
    return SpaceCatalog(
        objects=[
            SpaceObject(name="rx_fill", relation="rx_fill", key=["fill_id"]),
            SpaceObject(name="rx_counter", relation="rx_counter", key=["counter_id"]),
        ],
        links=[
            SpaceLink(
                name="fill_to_counter",
                from_object="rx_fill",
                from_columns=["counter_id"],
                to_object="rx_counter",
                to_columns=["counter_id"],
                cardinality="many_to_one",
            )
        ],
        measures=[
            SpaceMeasure(
                name="fill_count",
                grain="rx_fill",
                expression="SELECT COUNT(*) AS n FROM rx_fill",
                description="count of prescription fills",
            )
        ],
        definitions=[
            SpaceDefinition(
                name="fill_count",
                grain="rx_fill",
                text="count of prescription fills",
            )
        ],
        granted_tables=["rx_fill", "rx_counter"],
    )


def _catalog_b(*, measures: bool = True) -> SpaceCatalog:
    measure_list = [
        SpaceMeasure(
            name="visit_count",
            grain="clinic_visit",
            expression="SELECT COUNT(*) AS n FROM clinic_visit",
            description="count of clinic visits",
        )
    ]
    definition_list = [
        SpaceDefinition(
            name="visit_count",
            grain="clinic_visit",
            text="count of clinic visits",
        )
    ]
    return SpaceCatalog(
        objects=[
            SpaceObject(name="clinic_visit", relation="clinic_visit", key=["visit_id"]),
            SpaceObject(name="clinic_room", relation="clinic_room", key=["room_id"]),
        ],
        links=[
            SpaceLink(
                name="visit_to_room",
                from_object="clinic_visit",
                from_columns=["room_id"],
                to_object="clinic_room",
                to_columns=["room_id"],
                cardinality="many_to_one",
            )
        ],
        measures=measure_list if measures else [],
        definitions=definition_list if measures else [],
        granted_tables=["clinic_visit", "clinic_room"],
    )


def _verified(
    predicates: dict[str, str],
    *,
    space_id: str,
    catalog: SpaceCatalog | None,
) -> VerifiedManifest:
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id=f"sess-{uuid.uuid4().hex}",
        org_id="synthetic",
        space_id=space_id,
        pool_id="default",
        issuer_key_id="int-test",
        allowed_paths=["/data/pool/synthetic/**"],
        row_predicates=predicates,
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
        space_catalog=catalog,
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-test", verified_at=when)


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv("CORTEX_SPACE_CATALOG", "1")
    else:
        monkeypatch.delenv("CORTEX_SPACE_CATALOG", raising=False)
    monkeypatch.delenv("DMS_SPACE_CATALOG", raising=False)


@pytest.fixture
def choice_port():
    from CortexOS.dms.metric_choice import clear_metric_choice, register_metric_choice
    from packs.dms.metric_choice import OpenVaultMetricChoice

    register_metric_choice(OpenVaultMetricChoice())
    yield
    clear_metric_choice()


class _Script:
    def __init__(self, choose: dict[str, Any], check: dict[str, Any] | None = None) -> None:
        self.choose = choose
        self.check = check or {"agrees": True, "unsure": False}
        self.messages: list[dict[str, Any]] = []

    def complete(self, task: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from CortexOS.dms.metric_choice import CHECK_TASK, CHOOSE_TASK
        from CortexOS.integrations.freeroute import Completion

        self.messages.append({"task": task, "messages": list(messages)})
        if task == CHOOSE_TASK:
            body = self.choose
        elif task == CHECK_TASK:
            body = self.check
        else:
            return Completion(ok=False, text="", reason="unexpected task")
        return Completion(ok=True, text=json.dumps(body))


def _arm(monkeypatch: pytest.MonkeyPatch, script: _Script, *, armed: bool = True) -> None:
    from CortexOS.integrations import freeroute
    from CortexOS.integrations.freeroute import Arming

    monkeypatch.setattr(
        freeroute,
        "arming",
        lambda: Arming(armed=armed, reason="test", url="http://freeroute.invalid"),
    )
    monkeypatch.setattr(freeroute, "complete", script.complete)


def _prompt(script: _Script) -> str:
    parts: list[str] = []
    for call in script.messages:
        for message in call["messages"]:
            parts.append(str(message.get("content") or ""))
    return "\n".join(parts)


def _customer(result: dict[str, Any]) -> str:
    shown = {key: value for key, value in result.items() if key != "audit_id"}
    return json.dumps(shown, default=str)


def _assert_absent(text: str, names: tuple[str, ...]) -> None:
    folded = text.casefold()
    found = [name for name in names if name.casefold() in folded]
    assert found == [], found


def test_null_space_catalog_does_not_change_canonical_bytes() -> None:
    when = datetime(2026, 11, 28, tzinfo=timezone.utc)
    manifest = Manifest(
        session_id="sess-bytes",
        org_id="synthetic",
        space_id="space-b",
        pool_id="default",
        issuer_key_id="int-test",
        allowed_paths=["/data/pool/synthetic/**"],
        row_predicates={"clinic_visit": "TRUE"},
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="sig",
    )
    raw = manifest.model_dump(mode="json")
    assert raw["space_catalog"] is None
    raw.pop("space_catalog")
    assert canonical_manifest_bytes(manifest) == canonical_manifest_bytes(raw)
    carrying = manifest.model_copy(update={"space_catalog": _catalog_b()})
    assert canonical_manifest_bytes(carrying) == canonical_manifest_bytes(raw)


def test_flag_off_envelope_matches_a_manifest_without_a_catalog(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from CortexOS.dms.answer_engine import answer

    _flag(monkeypatch, False)
    _arm(monkeypatch, _Script({}), armed=False)
    with_catalog = answer(
        QUESTION_B,
        session_id="off-with",
        space_id="space-b",
        verified=_verified(
            {"clinic_visit": "TRUE"},
            space_id="space-b",
            catalog=_catalog_b(),
        ),
        require_grounding=True,
    )
    without = answer(
        QUESTION_B,
        session_id="off-without",
        space_id="space-b",
        verified=_verified(
            {"clinic_visit": "TRUE"},
            space_id="space-b",
            catalog=None,
        ),
        require_grounding=True,
    )
    assert _customer(with_catalog) == _customer(without)
    assert with_catalog.get("space_catalog_handoff") is None
    assert "not armed" in str(with_catalog.get("answer"))


def test_flag_off_prompt_is_the_demo_catalog(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.space_catalog import current_space_catalog

    _flag(monkeypatch, False)
    script = _Script(
        {
            "metric_id": "none",
            "why": "nothing in the listed metrics",
            "closest_question": "",
        }
    )
    _arm(monkeypatch, script)
    verified = _verified(
        {"clinic_visit": "TRUE"},
        space_id="space-b",
        catalog=_catalog_b(),
    )
    answer(
        QUESTION_B,
        session_id="off-prompt",
        space_id="space-b",
        verified=verified,
        require_grounding=True,
    )
    prompt = _prompt(script)
    assert current_space_catalog() is None
    assert "inventory" in prompt.casefold()
    _assert_absent(prompt, SPACE_A_NAMES + SPACE_B_NAMES)


def test_missing_catalog_keeps_the_demo_prompt(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from CortexOS.dms.answer_engine import answer

    demo = _Script(
        {"metric_id": "none", "why": "nothing in the listed metrics", "closest_question": ""}
    )
    _flag(monkeypatch, False)
    _arm(monkeypatch, demo)
    answer(
        QUESTION_B,
        session_id="demo-off",
        space_id="demo",
        verified=_verified(
            {"inventory": "TRUE"},
            space_id="demo",
            catalog=None,
        ),
        require_grounding=True,
    )
    off_prompt = _prompt(demo)

    on = _Script(
        {"metric_id": "none", "why": "nothing in the listed metrics", "closest_question": ""}
    )
    _flag(monkeypatch, True)
    _arm(monkeypatch, on)
    answer(
        QUESTION_B,
        session_id="demo-on",
        space_id="demo",
        verified=_verified(
            {"inventory": "TRUE"},
            space_id="demo",
            catalog=None,
        ),
        require_grounding=True,
    )
    assert _prompt(on) == off_prompt
    assert "inventory" in off_prompt.casefold()


def test_space_b_prompt_never_sees_space_a_or_the_demo_catalog(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from CortexOS.dms.answer_engine import answer

    _flag(monkeypatch, True)
    none = {
        "metric_id": "none",
        "why": "no declared measure means that question",
        "closest_question": "",
    }
    script_b = _Script(none)
    _arm(monkeypatch, script_b)
    answer(
        QUESTION_B,
        session_id="prompt-b",
        space_id="space-b",
        verified=_verified(
            {"clinic_visit": "TRUE", "clinic_room": "TRUE"},
            space_id="space-b",
            catalog=_catalog_b(),
        ),
        require_grounding=True,
    )
    prompt_b = _prompt(script_b)
    assert "clinic_visit" in prompt_b
    assert "visit_count" in prompt_b
    _assert_absent(prompt_b, SPACE_A_NAMES + DEMO_NAMES)

    script_a = _Script(none)
    _arm(monkeypatch, script_a)
    answer(
        "How many prescription fills were recorded?",
        session_id="prompt-a",
        space_id="space-a",
        verified=_verified(
            {"rx_fill": "TRUE", "rx_counter": "TRUE"},
            space_id="space-a",
            catalog=_catalog_a(),
        ),
        require_grounding=True,
    )
    prompt_a = _prompt(script_a)
    assert "rx_fill" in prompt_a
    assert "fill_count" in prompt_a
    _assert_absent(prompt_a, SPACE_B_NAMES + DEMO_NAMES)


def test_space_b_reason_drops_foreign_names(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.metric_choice import NO_CLOSEST

    _flag(monkeypatch, True)
    script = _Script(
        {
            "metric_id": "none",
            "why": "Insurance claims are not in rx_fill or the inventory table",
            "closest_question": "How many rows are in rx_fill?",
        }
    )
    _arm(monkeypatch, script)
    result = answer(
        QUESTION_B,
        session_id="reason-b",
        space_id="space-b",
        verified=_verified(
            {"clinic_visit": "TRUE", "clinic_room": "TRUE"},
            space_id="space-b",
            catalog=_catalog_b(),
        ),
        require_grounding=True,
    )
    shown = _customer(result)
    _assert_absent(shown, SPACE_A_NAMES + DEMO_NAMES)
    assert "clinic visits" in shown
    assert result["route"] == "reconfirm"
    assert result["suggestions"] == [NO_CLOSEST]
    assert result["reconfirm"]["closest_question"] == NO_CLOSEST
    assert result.get("space_catalog_handoff") is None


def test_space_with_no_measures_hands_off(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from cortex_contract.answer import Answer, Badge

    from CortexOS.api.contract_routes import _enrich_answer
    from CortexOS.dms.answer_engine import answer

    _flag(monkeypatch, True)
    script = _Script({"metric_id": "visit_count"})
    _arm(monkeypatch, script)
    verified = _verified(
        {"clinic_visit": "TRUE"},
        space_id="space-b",
        catalog=_catalog_b(measures=False),
    )
    result = answer(
        QUESTION_B,
        session_id="handoff-b",
        space_id="space-b",
        verified=verified,
        require_grounding=True,
    )
    assert script.messages == []
    assert result["route"] == "generative"
    assert result["space_catalog_handoff"] == "generative"
    assert result["reconfirm"] is None
    assert result["sql_used"] is None
    assert "not armed" not in str(result["answer"])
    _assert_absent(_customer(result), SPACE_A_NAMES + DEMO_NAMES)

    enriched = _enrich_answer(dict(result), session_id="handoff-b", verified=verified)
    wire = Answer.model_validate(enriched)
    dumped = wire.model_dump(mode="json")
    assert dumped["space_catalog_handoff"] == "generative"
    assert dumped["route"] == "generative"
    assert dumped["reconfirm"] is None
    assert dumped["provenance"]["badge"] == Badge.ABSTAIN.value


def test_false_handoff_is_omitted(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    from cortex_contract.answer import Answer

    from CortexOS.api.contract_routes import _enrich_answer
    from CortexOS.dms.answer_engine import answer

    _flag(monkeypatch, False)
    _arm(monkeypatch, _Script({}), armed=False)
    verified = _verified({"inventory": "TRUE"}, space_id="demo", catalog=None)
    result = answer(
        QUESTION_B,
        session_id="omit-off",
        space_id="demo",
        verified=verified,
        require_grounding=True,
    )
    enriched = _enrich_answer(dict(result), session_id="omit-off", verified=verified)
    dumped = Answer.model_validate(enriched).model_dump(mode="json")
    assert "space_catalog_handoff" not in dumped


def test_space_b_serves_its_own_definition(
    monkeypatch: pytest.MonkeyPatch, choice_port: None, tmp_path
) -> None:
    import duckdb

    from CortexOS.dms.answer_engine import answer

    db = tmp_path / "clinic.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE clinic_visit (visit_id INTEGER)")
    con.execute("INSERT INTO clinic_visit VALUES (1), (2)")
    con.close()
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    import CortexOS.execution.submit as submit

    monkeypatch.setattr(submit, "DEFAULT_DB", db)

    _flag(monkeypatch, True)
    script = _Script(
        {
            "metric_id": "visit_count",
            "why": "the question asks for clinic visits",
            "closest_question": "",
            "slots": {},
        },
        {"agrees": True, "unsure": False},
    )
    _arm(monkeypatch, script)
    result = answer(
        QUESTION_B,
        session_id="serve-b",
        space_id="space-b",
        verified=_verified(
            {"clinic_visit": "TRUE", "clinic_room": "TRUE"},
            space_id="space-b",
            catalog=_catalog_b(),
        ),
        require_grounding=True,
    )
    prompt = _prompt(script)
    _assert_absent(prompt, SPACE_A_NAMES + DEMO_NAMES)
    _assert_absent(_customer(result), SPACE_A_NAMES + DEMO_NAMES)
    assert result["rows"]
    cell = next(iter(result["rows"][0].values()))
    assert int(cell) == 2
    assert result["sql_used"]
    assert "clinic_visit" in result["sql_used"]
    assert "rx_fill" not in result["sql_used"]
    assert result["answer"]
