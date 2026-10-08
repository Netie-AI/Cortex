"""SCHEMA-RETRIEVE (#306) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives. Off (the default) the route
never loads a catalog and the JSON carries no ``schema_retrieval`` key, so the
bytes are the 1.4 answer's. On, the stamp is bounded by the signed grant and the
grant's Space, and the answer itself is unchanged. No model is called.
"""

from __future__ import annotations

import base64
import itertools
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import Answer
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import create_model

from CortexOS.dms import schema_retrieve_ask as sra
from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes
from CortexOS.memory import space_memory as sm
from CortexOS.memory.space_memory import Actor, reset_space_memory_for_tests

SESSION = "schema-retrieve-306"
REVENUE_Q = "what is our total revenue"
DELAYED_Q = "which suppliers have delayed shipments"
STEWARD = Actor("steward-alice", "steward")


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


@pytest.fixture
def ask_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from bench.accuracy import _ensure_db_loaded
    from CortexOS.api.app import create_app
    from CortexOS.dms.answer_engine import clear_session
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
    from CortexOS.execution.session_manifests import (
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from CortexOS.execution.submit import set_verifier_for_tests
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    for name in (sra.ENABLED_ENV, sm.ENABLED_ENV, sm.SCORED_ROUND_ENV):
        monkeypatch.delenv(name, raising=False)
    import netie.config

    netie.config._cached_config = None
    _ensure_db_loaded()
    reload()

    issuer = Ed25519PrivateKey.generate()
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer)]})
    verifier = ManifestVerifier(cache)
    set_verifier_for_tests(verifier)
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    reset_limiter(10_000)
    reset_space_memory_for_tests()

    def _bind(session_id: str, space_id: str, grant: dict[str, str]) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=session_id,
            org_id="acme",
            space_id=space_id,
            pool_id="default",
            issuer_key_id="int-1",
            allowed_paths=["/data/pool/acme/**"],
            row_predicates=grant,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            signature="",
        )
        manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
        get_session_registry().bind(verifier.verify(manifest))
        clear_session(session_id, space_id=space_id)

    client = TestClient(create_app())
    client.bind_session = _bind  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    reset_space_memory_for_tests()
    netie.config._cached_config = None


def _post(client, space_id: str, question: str = REVENUE_Q, **extra: Any):
    resp = client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SESSION, "space_id": space_id, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp


def _ask(client, space_id: str, question: str = REVENUE_Q, **extra: Any) -> dict[str, Any]:
    return _post(client, space_id, question, **extra).json()


def _names(stamp: dict[str, Any]) -> set[str]:
    names = {c["table"] for c in stamp["served_candidates"]}
    names |= {t["table"] for t in stamp["served_chosen"]}
    for path in stamp["served_join_paths"]:
        names |= set(path["tables"])
    return names


@pytest.fixture
def frozen_ids(monkeypatch: pytest.MonkeyPatch):
    """Deterministic uuid4 and token expiry so two responses can be compared byte for byte."""
    from CortexOS.execution import drillthrough

    counter = itertools.count()
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))
    monkeypatch.setattr(drillthrough.time, "time", lambda: 1_790_000_000.0)

    def reset() -> None:
        nonlocal counter
        counter = itertools.count()

    return reset


# -- off: inert and byte-identical ----------------------------------------


def test_flag_off_never_loads_a_catalog_or_touches_memory(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.schema_retrieve import catalog, core

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("schema retrieval ran with CORTEX_SCHEMA_RETRIEVE unset")

    monkeypatch.setattr(catalog, "load_pack_catalog", boom)
    monkeypatch.setattr(core.DeterministicSchemaRetriever, "retrieve", boom)
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    ask_http.bind_session(SESSION, "alpha", {"transactions": "TRUE"})

    body = _ask(ask_http, "alpha")
    assert "schema_retrieval" not in body
    assert body["rows"] and body["sql_used"]
    ops = [s.served_op for s in sm.get_space_memory().stamps(space_id="alpha")]
    assert ops == ["read"], "only C-MEM's own solution lookup may touch memory"


def test_flag_off_bytes_are_the_1_4_answer(ask_http, frozen_ids) -> None:
    """Re-serialise the response with a 1.4 ``Answer`` (no 1.5 field): same bytes."""
    fields = {
        n: (f.annotation, f) for n, f in Answer.model_fields.items() if n != "schema_retrieval"
    }
    answer_14 = create_model("Answer", **fields)  # type: ignore[call-overload]
    ask_http.bind_session(SESSION, "alpha", {"transactions": "TRUE"})

    for question in (REVENUE_Q, DELAYED_Q, "how many skus"):
        frozen_ids()
        raw = _post(ask_http, "alpha", question).content
        body = json.loads(raw)
        as_14 = answer_14.model_validate(body).model_dump(mode="json")
        rendered = json.dumps(
            as_14, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")
        ).encode("utf-8")
        assert raw == rendered, question
        assert list(body) == list(fields)


def test_flag_on_stamps_without_changing_the_answer(
    ask_http, monkeypatch: pytest.MonkeyPatch, frozen_ids
) -> None:
    ask_http.bind_session(SESSION, "alpha", {"transactions": "TRUE", "inventory": "TRUE"})
    frozen_ids()
    off = _ask(ask_http, "alpha")

    monkeypatch.setenv(sra.ENABLED_ENV, "1")
    frozen_ids()
    on = _ask(ask_http, "alpha")

    stamp = on.pop("schema_retrieval")
    assert on == off, "retrieval must not change the answer (planning is #291/#303)"
    assert stamp["served_op"] == "schema_retrieve"
    assert stamp["served_space_id"] == "alpha"
    assert stamp["served_method"] == "deterministic"
    assert stamp["served_by"] == sra.ASK_ACTOR and stamp["served_at"]
    assert _names(stamp) <= {"transactions", "inventory"}
    assert stamp["served_memory_ids_read"] == [] and stamp["served_memory_ids_written"] == []


# -- must-fails over HTTP ---------------------------------------------------


def test_must_fail_contract_ask_never_returns_a_table_outside_the_grant(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sra.ENABLED_ENV, "1")
    ask_http.bind_session(SESSION, "alpha", {"transactions": "TRUE", "inventory": "TRUE"})

    body = _ask(ask_http, "alpha", DELAYED_Q + " and their inventory")
    stamp = body["schema_retrieval"]
    assert stamp["served_chosen"], "control: inventory is granted and matches"
    assert _names(stamp) <= {"transactions", "inventory"}
    raw = json.dumps(stamp)
    for ungranted in ("suppliers", "shipments", "locations", "alerts"):
        assert ungranted not in raw, f"{ungranted} leaked: {raw}"


def test_must_fail_contract_ask_space_b_never_sees_space_a_tables(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sra.ENABLED_ENV, "1")
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    grant = {"transactions": "TRUE", "fx_rates": "TRUE"}
    ask_http.bind_session(SESSION, "alpha", grant)
    ask_http.bind_session(SESSION, "beta", grant)
    entry, _ = sm.get_space_memory().write(
        space_id="alpha",
        kind="table",
        key="fx_rates",
        body={
            "columns": ["currency", "rate_myr"],
            "keys": ["currency"],
            "joins": [],
            "row_count": 3,
        },
        source="schema:connected_db/fx_rates",
        actor="cortex:schema-scan",
    )
    question = "fx rate by currency"

    alpha = _ask(ask_http, "alpha", question)["schema_retrieval"]
    assert "fx_rates" in {t["table"] for t in alpha["served_chosen"]}, "control"
    assert alpha["served_memory_ids_read"] == [entry.id]

    beta = _ask(ask_http, "beta", question)["schema_retrieval"]
    assert beta["served_space_id"] == "beta"
    assert "fx_rates" not in json.dumps(beta)
    assert entry.id not in beta["served_memory_ids_read"]


def test_must_fail_contract_ask_scored_pack_never_writes_memory(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sra.ENABLED_ENV, "1")
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    ask_http.bind_session(SESSION, "alpha", {"inventory": "TRUE", "transactions": "TRUE"})
    ask_http.bind_session(SESSION, "beta", {"inventory": "TRUE", "transactions": "TRUE"})

    scored = _ask(ask_http, "alpha", "how many skus", scored_pack_id="curated_ceo")
    stamp = scored["schema_retrieval"]
    assert stamp["served_chosen"][0]["table"] == "inventory"
    assert stamp["served_memory_ids_written"] == []
    assert stamp["served_memory_refusals"] == [sm.SCORED_PACK_WRITE]
    assert sm.get_space_memory().steward_view(space_id="alpha", steward=STEWARD)[0] == []

    learned = _ask(ask_http, "beta", "how many skus")["schema_retrieval"]
    written = learned["served_memory_ids_written"]
    assert written and len(written) == len(learned["served_chosen"]), "control: unscored ask learns"
    assert learned["served_memory_refusals"] == []
