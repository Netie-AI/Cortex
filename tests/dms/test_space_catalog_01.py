"""FDE-CORTEX-SPACE-CATALOG-01: the chooser reads the asking Space only.

Two synthetic Spaces with disjoint schemas. A question on Space B must not
see Space A's catalog, or the demo supply-chain catalog, in the prompt, the
reason, or the suggestions. Flag off, a manifest that carries a Space catalog
answers the same as a manifest that does not.
"""

from __future__ import annotations

import base64
import json
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    canonical_space_catalog_bytes,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.execution.manifest import (
    CATALOG_SIG_INVALID,
    CATALOG_SIG_MISSING,
    JwksCache,
    ManifestVerifier,
    VerifiedManifest,
)

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


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
        "kid": kid,
        "x": _b64u(raw),
    }


_ISSUER: Ed25519PrivateKey | None = None
_CACHE: JwksCache | None = None


def _issuer() -> tuple[Ed25519PrivateKey, JwksCache]:
    """One ephemeral issuer for this process. The key is not written to the repo."""
    global _ISSUER, _CACHE
    if _ISSUER is None or _CACHE is None:
        _ISSUER = Ed25519PrivateKey.generate()
        _CACHE = JwksCache(path=Path(tempfile.mkdtemp()) / "jwks.json")
        _CACHE.install({"keys": [_jwk("int-test", _ISSUER)]})
    return _ISSUER, _CACHE


def _manifest(
    predicates: dict[str, str],
    *,
    space_id: str,
    catalog: SpaceCatalog | None,
    session_id: str | None = None,
) -> Manifest:
    when = datetime.now(timezone.utc)
    return Manifest(
        session_id=session_id or f"sess-{uuid.uuid4().hex}",
        org_id="synthetic",
        space_id=space_id,
        pool_id="default",
        issuer_key_id="int-test",
        allowed_paths=["/data/pool/synthetic/**"],
        row_predicates=predicates,
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="",
        space_catalog=catalog,
    )


def _sign(manifest: Manifest, *, catalog: bool = True) -> Manifest:
    """Sign with the manifest issuer key. ``catalog=False`` leaves the catalog unsigned."""
    private, _cache = _issuer()
    if catalog and manifest.space_catalog is not None:
        manifest.space_catalog_sig = _b64u(
            private.sign(
                canonical_space_catalog_bytes(
                    manifest.space_catalog,
                    space_id=manifest.space_id or "",
                    session_id=manifest.session_id,
                )
            )
        )
    manifest.signature = _b64u(private.sign(canonical_manifest_bytes(manifest)))
    return manifest


def _verify(manifest: Manifest) -> VerifiedManifest:
    _private, cache = _issuer()
    return ManifestVerifier(cache).verify(manifest)


def _verified(
    predicates: dict[str, str],
    *,
    space_id: str,
    catalog: SpaceCatalog | None,
    session_id: str | None = None,
    sign_catalog: bool = True,
) -> VerifiedManifest:
    return _verify(
        _sign(
            _manifest(
                predicates,
                space_id=space_id,
                catalog=catalog,
                session_id=session_id,
            ),
            catalog=sign_catalog,
        )
    )


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
    signed_beside = carrying.model_copy(update={"space_catalog_sig": "abc"})
    assert canonical_manifest_bytes(signed_beside) == canonical_manifest_bytes(raw)
    payload = json.loads(
        canonical_space_catalog_bytes(
            _catalog_b(), space_id="space-b", session_id="sess-bytes"
        )
    )
    assert set(payload) == {"session_id", "space_catalog", "space_id"}
    assert payload["space_id"] == "space-b"
    assert payload["session_id"] == "sess-bytes"


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


def test_unsigned_granted_tables_cannot_widen_or_name_signed_grants(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    """The catalog may not name a table the signature did not grant.

    Signed grant is clinic_visit only. The unsigned catalog also lists
    payroll_secret and a measure whose SQL reads it. That name must not
    reach the gate, the listing, the chooser prompt, or the envelope.
    """
    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.space_catalog import (
        current_space_catalog,
        gate_semantic,
        listing_text,
        open_space_catalog,
    )

    extra = "payroll_secret"
    catalog = SpaceCatalog(
        objects=[
            SpaceObject(name="clinic_visit", relation="clinic_visit", key=["visit_id"]),
            SpaceObject(name=extra, relation=extra, key=["emp_id"]),
        ],
        links=[
            SpaceLink(
                name="visit_to_pay",
                from_object="clinic_visit",
                from_columns=["emp_id"],
                to_object=extra,
                to_columns=["emp_id"],
                cardinality="many_to_one",
            )
        ],
        measures=[
            SpaceMeasure(
                name="visit_count",
                grain="clinic_visit",
                expression="SELECT COUNT(*) AS n FROM clinic_visit",
                description="count of clinic visits",
            ),
            SpaceMeasure(
                name="pay_count",
                grain=extra,
                expression=f"SELECT COUNT(*) AS n FROM {extra}",
                description="count of payroll rows",
            ),
        ],
        definitions=[
            SpaceDefinition(
                name="pay_count",
                grain=extra,
                text=f"SELECT COUNT(*) AS n FROM {extra}",
            )
        ],
        granted_tables=["clinic_visit", extra],
    )
    verified = _verified({"clinic_visit": "TRUE"}, space_id="space-b", catalog=catalog)
    _flag(monkeypatch, True)
    script = _Script(
        {"metric_id": "catalog", "why": "", "closest_question": ""},
        {"agrees": True, "unsure": False},
    )
    _arm(monkeypatch, script)
    result = answer(
        QUESTION_B,
        session_id="widen-b",
        space_id="space-b",
        verified=verified,
        require_grounding=True,
    )
    assert result["sql_used"] is None
    assert not result["rows"]
    _assert_absent(_customer(result), (extra,))
    _assert_absent(_prompt(script), (extra,))
    _assert_absent(str(result.get("answer") or ""), (extra,))

    with open_space_catalog(verified):
        bound = current_space_catalog()
        assert bound is not None
        semantic = gate_semantic()
        assert semantic is not None
        signed = {name.split(".")[-1].lower() for name in verified.manifest.row_predicates}
        assert {key.lower() for key in semantic["tables"]} <= signed
        assert extra not in semantic["tables"]
        listing = listing_text(bound)
        _assert_absent(listing, (extra,))
        assert "clinic_visit" in listing


def _surfaces(verified: VerifiedManifest, result: dict[str, Any], script: _Script) -> str:
    from CortexOS.dms.space_catalog import (
        chooser_blocks,
        current_space_catalog,
        gate_semantic,
        listing_text,
        open_space_catalog,
    )

    with open_space_catalog(verified):
        bound = current_space_catalog()
        assert bound is not None
        subjects, metrics = chooser_blocks(bound)
        semantic = gate_semantic()
        assert semantic is not None
        listing = listing_text(bound)
        gate_names = " ".join(semantic["tables"])
    return "\n".join(
        (
            _customer(result),
            _prompt(script),
            subjects,
            metrics,
            listing,
            gate_names,
            str(result.get("answer") or ""),
            str(result.get("assumptions") or ""),
            json.dumps(result.get("suggestions") or []),
            json.dumps(result.get("reconfirm") or {}),
        )
    )


def test_unsigned_object_name_cannot_enter_gate_or_chooser(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    """An object name is not a table just because its relation is signed."""
    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.space_catalog import current_space_catalog, gate_semantic, open_space_catalog

    extra = "payroll_secret"
    catalog = SpaceCatalog(
        objects=[
            SpaceObject(name="clinic_visit", relation="clinic_visit", key=["visit_id"]),
            SpaceObject(name=extra, relation="clinic_visit", key=["emp_id"]),
        ],
        measures=[
            SpaceMeasure(
                name="visit_count",
                grain="clinic_visit",
                expression="SELECT COUNT(*) AS n FROM clinic_visit",
                description="count of clinic visits",
            )
        ],
        granted_tables=["clinic_visit"],
    )
    verified = _verified({"clinic_visit": "TRUE"}, space_id="space-b", catalog=catalog)
    _flag(monkeypatch, True)
    script = _Script(
        {"metric_id": "catalog", "why": "", "closest_question": ""},
        {"agrees": True, "unsure": False},
    )
    _arm(monkeypatch, script)
    result = answer(
        QUESTION_B,
        session_id="object-name",
        space_id="space-b",
        verified=verified,
        require_grounding=True,
    )
    shown = _surfaces(verified, result, script)
    _assert_absent(shown, (extra,))
    with open_space_catalog(verified):
        semantic = gate_semantic()
        assert semantic is not None
        signed = {name.split(".")[-1].lower() for name in verified.manifest.row_predicates}
        assert {key.lower() for key in semantic["tables"]} <= signed
        bound = current_space_catalog()
        assert bound is not None
        assert extra not in {obj.name for obj in bound.objects}


def test_unsigned_name_in_every_field_stays_out_of_the_answer(
    monkeypatch: pytest.MonkeyPatch, choice_port: None
) -> None:
    """Every catalog field can carry an unsigned name. None of them is shown."""
    from CortexOS.dms.answer_engine import answer

    planted = {
        "granted": "u_granted",
        "object_name": "u_object_name",
        "relation": "u_relation",
        "key": "u_key",
        "link": "u_link",
        "link_from": "u_link_from",
        "link_to": "u_link_to",
        "from_col": "u_from_col",
        "to_col": "u_to_col",
        "cardinality": "u_card",
        "measure": "u_measure",
        "grain": "u_grain",
        "expr": "u_expr",
        "desc": "u_desc",
        "unparsed": "u_unparsed",
        "definition": "u_definition",
        "def_grain": "u_def_grain",
        "def_text": "u_def_text",
    }
    catalog = SpaceCatalog(
        objects=[
            SpaceObject(name="clinic_visit", relation="clinic_visit", key=["visit_id"]),
            SpaceObject(
                name=planted["object_name"],
                relation="clinic_visit",
                key=[planted["key"]],
            ),
            SpaceObject(
                name="clinic_visit",
                relation=planted["relation"],
                key=["visit_id"],
            ),
        ],
        links=[
            SpaceLink(
                name=planted["link"],
                from_object=planted["link_from"],
                from_columns=[planted["from_col"]],
                to_object=planted["link_to"],
                to_columns=[planted["to_col"]],
                cardinality=planted["cardinality"],
            )
        ],
        measures=[
            SpaceMeasure(
                name="visit_count",
                grain="clinic_visit",
                expression="SELECT COUNT(*) AS n FROM clinic_visit",
                description="count of clinic visits",
            ),
            SpaceMeasure(
                name=planted["measure"],
                grain=planted["grain"],
                expression=f"SELECT COUNT(*) AS n FROM {planted['expr']}",
                description=planted["desc"],
            ),
            SpaceMeasure(
                name="u_bad_sql",
                grain="clinic_visit",
                expression=f"not sql {planted['unparsed']}",
                description=planted["unparsed"],
            ),
        ],
        definitions=[
            SpaceDefinition(
                name="visit_count",
                grain=planted["def_grain"],
                text=f"not sql {planted['def_text']}",
            ),
            SpaceDefinition(
                name=planted["definition"],
                grain="clinic_visit",
                text=f"SELECT COUNT(*) AS n FROM {planted['definition']}",
            ),
        ],
        granted_tables=["clinic_visit", planted["granted"]],
    )
    verified = _verified({"clinic_visit": "TRUE"}, space_id="space-b", catalog=catalog)
    _flag(monkeypatch, True)
    script = _Script(
        {"metric_id": "catalog", "why": "", "closest_question": ""},
        {"agrees": True, "unsure": False},
    )
    _arm(monkeypatch, script)
    result = answer(
        QUESTION_B,
        session_id="every-field",
        space_id="space-b",
        verified=verified,
        require_grounding=True,
    )
    shown = _surfaces(verified, result, script)
    _assert_absent(shown, tuple(planted.values()))
    assert "clinic_visit" in shown
    assert result["sql_used"] is None
    assert not result["rows"]


def _kept_row_catalog(extra: str) -> SpaceCatalog:
    """A catalog intersection would keep, with ``extra`` only in free text."""
    sql = f"SELECT COUNT(*) AS {extra} FROM clinic_visit /* {extra} */"
    return SpaceCatalog(
        objects=[
            SpaceObject(name="clinic_visit", relation="clinic_visit", key=[extra]),
        ],
        links=[
            SpaceLink(
                name=extra,
                from_object="clinic_visit",
                from_columns=[extra],
                to_object="clinic_visit",
                to_columns=[extra],
                cardinality=extra,
            )
        ],
        measures=[
            SpaceMeasure(
                name=extra,
                grain="clinic_visit",
                expression=sql,
                description=extra,
            )
        ],
        definitions=[
            SpaceDefinition(name=extra, grain="clinic_visit", text=sql),
        ],
        granted_tables=["clinic_visit"],
    )


def _resign_manifest(manifest: Manifest) -> Manifest:
    """Replace the manifest signature. Leaves ``space_catalog_sig`` as it is."""
    private, _cache = _issuer()
    manifest.signature = _b64u(private.sign(canonical_manifest_bytes(manifest)))
    return manifest


def test_unsigned_name_on_kept_rows_stays_out_of_the_answer(
    monkeypatch: pytest.MonkeyPatch, choice_port: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A tampered catalog with no signature is not used at all."""
    import logging

    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.space_catalog import current_space_catalog, gate_semantic, open_space_catalog

    extra = "payroll_secret"
    caplog.set_level(logging.WARNING)
    verified = _verified(
        {"clinic_visit": "TRUE"},
        space_id="space-b",
        catalog=_kept_row_catalog(extra),
        sign_catalog=False,
    )
    assert verified.catalog_trusted is False
    assert verified.catalog_refusal == CATALOG_SIG_MISSING
    assert CATALOG_SIG_MISSING in caplog.text
    _flag(monkeypatch, True)
    script = _Script(
        {"metric_id": "catalog", "why": extra, "closest_question": extra},
        {"agrees": True, "unsure": False},
    )
    _arm(monkeypatch, script)
    result = answer(
        QUESTION_B,
        session_id="kept-row",
        space_id="space-b",
        verified=verified,
        require_grounding=True,
    )
    shown = "\n".join(
        (
            _customer(result),
            _prompt(script),
            str(result.get("answer") or ""),
            str(result.get("assumptions") or ""),
            json.dumps(result.get("suggestions") or []),
            json.dumps(result.get("reconfirm") or {}),
        )
    )
    _assert_absent(shown, (extra,))
    with open_space_catalog(verified):
        assert current_space_catalog() is None
        assert gate_semantic() is None


def test_catalog_signature_tamper_rejected(caplog: pytest.LogCaptureFixture) -> None:
    """One changed byte, or a catalog signed for another space or session, is refused."""
    import logging

    caplog.set_level(logging.WARNING)
    catalog = _kept_row_catalog("payroll_secret")
    good = _sign(
        _manifest(
            {"clinic_visit": "TRUE"},
            space_id="space-b",
            catalog=catalog,
            session_id="sess-good",
        )
    )
    assert _verify(good).catalog_trusted is True

    flipped = good.space_catalog_sig or ""
    last = "A" if not flipped.endswith("A") else "B"
    one_byte = good.model_copy(update={"space_catalog_sig": flipped[:-1] + last})
    refused = _verify(_resign_manifest(one_byte))
    assert refused.catalog_trusted is False
    assert refused.catalog_refusal == CATALOG_SIG_INVALID

    other_space = _sign(
        _manifest(
            {"clinic_visit": "TRUE"},
            space_id="space-a",
            catalog=catalog,
            session_id="sess-good",
        ),
        catalog=False,
    )
    other_space.space_catalog_sig = good.space_catalog_sig
    replayed_space = _verify(_resign_manifest(other_space))
    assert replayed_space.catalog_trusted is False
    assert replayed_space.catalog_refusal == CATALOG_SIG_INVALID

    other_session = _sign(
        _manifest(
            {"clinic_visit": "TRUE"},
            space_id="space-b",
            catalog=catalog,
            session_id="sess-other",
        ),
        catalog=False,
    )
    other_session.space_catalog_sig = good.space_catalog_sig
    replayed_session = _verify(_resign_manifest(other_session))
    assert replayed_session.catalog_trusted is False
    assert replayed_session.catalog_refusal == CATALOG_SIG_INVALID
    assert CATALOG_SIG_INVALID in caplog.text


def test_gate_semantic_columns_come_from_the_signed_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed catalog key is not a column. Columns come from the grant's schema."""
    from CortexOS.dms.space_catalog import gate_semantic, open_space_catalog
    from CortexOS.execution.submit import granted_column_names

    extra = "payroll_secret"
    _flag(monkeypatch, True)
    verified = _verified(
        {"clinic_visit": "TRUE"},
        space_id="space-b",
        catalog=_kept_row_catalog(extra),
    )
    assert verified.catalog_trusted is True
    real = {name.casefold() for name in granted_column_names(verified)}
    with open_space_catalog(verified):
        semantic = gate_semantic()
    assert semantic is not None
    assert set(semantic["tables"]) == {"clinic_visit"}
    for spec in semantic["tables"].values():
        got = {str(name).casefold() for name in spec["columns"]}
        assert extra not in got
        if real:
            assert got <= real
        else:
            assert got == {"_space_catalog"}
