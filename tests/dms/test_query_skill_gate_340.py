"""#340a: the legacy ``query_skill`` store is off by default, Space-scoped, and
never written during a scored round.

Assertions are on the HTTP envelope DMS receives from ``POST /v1/contract/ask``
(``provenance.layer``, rows, answer text) plus a row count read straight from
``dms_query_skills``. Env names are literals and the must-fails touch no new
API before their first assertion, so they fail on that assertion, not on an
import, when run against a tree without the gate.
"""

from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes

FLAG = "CORTEX_QUERY_SKILL"
SESSION = "qs-340"
GRANT = {"transactions": "TRUE"}
REVENUE_Q = "what is our total revenue"
# All four land on certified / governed_metric under GRANT, so each one is a
# capture candidate. Order matches the 87ee2c60 probe shape: 4 scored asks.
SCORED_QS = (
    "what is our total revenue",
    "Top 5 selling SKUs by revenue",
    "excluding BETA, top 5 sku by revenue",
    "last month sales",
)
BETA_QS = SCORED_QS + (
    "total revenue",
    "top 5 sku by revenue",
    "what were sales last month",
    "Top 5 selling SKUs by volume",
)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig", "kid": kid, "x": _b64u(raw)}


@pytest.fixture
def skills_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from packs.dms.semantic import query_skills

    db = tmp_path / "ops_qs.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv("DMS_QUERY_SKILL_CAPTURE", raising=False)
    monkeypatch.delenv("CORTEX_SCORED_ROUND", raising=False)
    query_skills.reset_schema_cache()
    yield db
    query_skills.reset_schema_cache()


@pytest.fixture
def ask_http(skills_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    from CortexOS.memory.space_memory import reset_space_memory_for_tests
    from packs.dms.security.rate_limit import reset_limiter
    from packs.dms.semantic.loader import reload

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.delenv("CORTEX_SPACE_MEMORY", raising=False)
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

    def _bind(space_id: str) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=SESSION,
            org_id="acme",
            space_id=space_id,
            pool_id="default",
            issuer_key_id="int-1",
            allowed_paths=["/data/pool/acme/**"],
            row_predicates=GRANT,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            signature="",
        )
        manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
        get_session_registry().bind(verifier.verify(manifest))
        clear_session(SESSION, space_id=space_id)

    client = TestClient(create_app())
    client.bind_session = _bind  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    reset_space_memory_for_tests()
    netie.config._cached_config = None


def _ask(client, space_id: str, question: str = REVENUE_Q, **extra: Any) -> dict[str, Any]:
    resp = client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SESSION, "space_id": space_id, **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rows(db: Path) -> int:
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db))
    try:
        found = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dms_query_skills'"
        ).fetchone()
        return 0 if found is None else int(con.execute("SELECT COUNT(*) FROM dms_query_skills").fetchone()[0])
    finally:
        con.close()


def _governed_answer(body: dict[str, Any]) -> None:
    assert body["provenance"]["layer"] in ("certified", "governed_metric"), body["provenance"]
    assert body["rows"] and body["answer"] and body["sql_used"], body


def _only_skills_can_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take L0/L1 out so a stored skill is the only path to rows."""
    from CortexOS.dms import answer_engine as ae

    monkeypatch.setattr(ae, "match_certified", lambda _q: None)
    monkeypatch.setattr(ae, "route_to_metric", lambda _q: None)


def _skill_answer(body: dict[str, Any]) -> None:
    assert body["provenance"]["layer"] == "query_skill", body["provenance"]
    assert float(body["rows"][0].get("revenue_myr") or 0) > 0, body
    assert body["answer"].strip()


def _not_skill_answer(body: dict[str, Any]) -> None:
    assert body["provenance"]["layer"] != "query_skill", body["provenance"]
    assert body["provenance"]["badge"] != "query_skill"
    assert not body["rows"], f"rows served without L0/L1 or a skill: {body['rows'][:2]}"


# ── flag ──────────────────────────────────────────────────────────────────────
def test_must_fail_flag_default_off_no_capture_no_read(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_http.bind_session("alpha")
    for q in SCORED_QS:
        _governed_answer(_ask(ask_http, "alpha", q))
    assert _rows(skills_db) == 0, "flag unset must capture nothing"

    monkeypatch.setenv(FLAG, "1")
    _governed_answer(_ask(ask_http, "alpha"))
    assert _rows(skills_db) == 1
    monkeypatch.delenv(FLAG)

    _only_skills_can_answer(monkeypatch)
    _not_skill_answer(_ask(ask_http, "alpha"))


def test_flag_on_unscored_alpha_asks_capture_in_alpha(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the row counter and the skill read can see a real write."""
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    for q in SCORED_QS:
        _governed_answer(_ask(ask_http, "alpha", q))
    assert _rows(skills_db) == len(SCORED_QS)

    _only_skills_can_answer(monkeypatch)
    _skill_answer(_ask(ask_http, "alpha"))


# ── (a) scored pack writes nothing ───────────────────────────────────────────
@pytest.mark.parametrize("scored_pack_id", ["curated_ceo", "prove_pack_dms"])
def test_must_fail_scored_pack_asks_write_zero_rows(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch, scored_pack_id: str
) -> None:
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    for q in SCORED_QS:
        _governed_answer(_ask(ask_http, "alpha", q, scored_pack_id=scored_pack_id))
    assert _rows(skills_db) == 0


def test_scored_round_env_writes_zero_rows(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("CORTEX_SCORED_ROUND", "1")
    ask_http.bind_session("alpha")
    for q in SCORED_QS:
        _governed_answer(_ask(ask_http, "alpha", q))
    assert _rows(skills_db) == 0


def test_scored_pack_ask_never_reads_a_stored_skill(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    _governed_answer(_ask(ask_http, "alpha"))
    assert _rows(skills_db) == 1

    _only_skills_can_answer(monkeypatch)
    _skill_answer(_ask(ask_http, "alpha"))
    _not_skill_answer(_ask(ask_http, "alpha", scored_pack_id="curated_ceo"))


# ── (b) no Space, no read ────────────────────────────────────────────────────
def test_must_fail_find_without_space_returns_nothing(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    _governed_answer(_ask(ask_http, "alpha"))
    assert _rows(skills_db) == 1

    assert query_skills.find(REVENUE_Q) is None
    assert query_skills.find(REVENUE_Q, space_id=None) is None
    assert query_skills.find(REVENUE_Q, space_id="  ") is None
    hit = query_skills.find(REVENUE_Q, space_id="alpha")
    assert hit is not None and hit["score"] == 1.0


def test_ask_scope_closes_after_the_request(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.dms.ask_scope import AskScope, current_ask_scope
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    _governed_answer(_ask(ask_http, "alpha", scored_pack_id="curated_ceo"))
    assert current_ask_scope() == AskScope()
    assert query_skills.capture(REVENUE_Q, metric_id="revenue_total") is None
    assert _rows(skills_db) == 0


def test_capture_without_space_writes_nothing(skills_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(FLAG, "1")
    assert query_skills.capture(REVENUE_Q, metric_id="revenue_total", sql=None) is None
    assert _rows(skills_db) == 0


# ── (c) Space isolation ──────────────────────────────────────────────────────
def test_must_fail_space_alpha_skill_never_visible_from_beta(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    ask_http.bind_session("beta")
    _governed_answer(_ask(ask_http, "alpha"))
    assert _rows(skills_db) == 1

    _only_skills_can_answer(monkeypatch)
    _skill_answer(_ask(ask_http, "alpha"))
    _not_skill_answer(_ask(ask_http, "beta"))
    assert query_skills.find(REVENUE_Q, space_id="beta") is None


def test_beta_capture_never_overwrites_alpha_row(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    ask_http.bind_session("beta")
    _governed_answer(_ask(ask_http, "alpha"))
    _governed_answer(_ask(ask_http, "beta"))
    con = sqlite3.connect(str(skills_db))
    try:
        rows = con.execute("SELECT space_id, support_count FROM dms_query_skills").fetchall()
    finally:
        con.close()
    assert rows == [("alpha", 1)]


def test_legacy_unscoped_rows_survive_and_are_never_read(
    skills_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Additive ALTER: a pre-#340 table keeps every row; NULL-Space rows stay dark."""
    from packs.dms.semantic import query_skills

    con = sqlite3.connect(str(skills_db))
    con.executescript(
        """
        CREATE TABLE dms_query_skills (
            id TEXT PRIMARY KEY, trigger_text TEXT NOT NULL,
            embedding TEXT NOT NULL DEFAULT '[]', metric_id TEXT,
            params_json TEXT NOT NULL DEFAULT '{}', sql_template TEXT,
            layer TEXT NOT NULL DEFAULT 'governed_metric',
            support_count INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT, created_at TEXT NOT NULL, UNIQUE (trigger_text)
        );
        INSERT INTO dms_query_skills (id, trigger_text, metric_id, created_at)
            VALUES ('legacy-1', 'what is our total revenue', 'revenue_total', '2026-10-01T00:00:00+00:00');
        """
    )
    con.commit()
    con.close()

    monkeypatch.setenv(FLAG, "1")
    assert query_skills.find(REVENUE_Q, space_id="alpha") is None
    assert query_skills.capture(REVENUE_Q, metric_id="revenue_total", space_id="alpha") is None

    con = sqlite3.connect(str(skills_db))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(dms_query_skills)")]
        rows = con.execute("SELECT id, space_id, support_count FROM dms_query_skills").fetchall()
    finally:
        con.close()
    assert cols[-1] == "space_id"
    assert rows == [("legacy-1", None, 1)]


# ── the 87ee2c60 probe, committed (#340) ─────────────────────────────────────
@pytest.mark.parametrize("flag", [None, "1"], ids=["flag-unset", "flag-on"])
def test_must_fail_probe_87ee2c60_scored_alpha_then_beta(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    """4 scored asks in alpha, then a no-Space find, then 8 asks from beta."""
    from packs.dms.semantic import query_skills

    if flag is not None:
        monkeypatch.setenv(FLAG, flag)
    ask_http.bind_session("alpha")
    ask_http.bind_session("beta")
    for q in SCORED_QS:
        _governed_answer(_ask(ask_http, "alpha", q, scored_pack_id="curated_ceo"))
    assert _rows(skills_db) == 0
    for q in SCORED_QS:
        assert query_skills.find(q) is None

    beta = [_ask(ask_http, "beta", q) for q in BETA_QS]
    assert [b["provenance"]["layer"] for b in beta].count("query_skill") == 0
    for b in beta:
        assert b["answer"].strip()
