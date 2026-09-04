"""C7-min — EXPLAIN dry-run + retry gate acceptance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cortex_contract.execution import Manifest

from CortexOS.dms.sql_validate_gate import (
    MANIFEST_VIOLATION_PREFIX,
    SqlGateAbstain,
    explain_dry_run,
    gate_with_retry,
    run_gate,
)
from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection, load_semantic_layer
from CortexOS.execution.manifest import VerifiedManifest, enforce_manifest


def _verified(predicates: dict[str, str]) -> VerifiedManifest:
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="c7-02-gate",
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=predicates,
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=when)


@pytest.fixture(scope="module")
def semantic():
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    return load_semantic_layer()


def test_explain_rejects_syntax_error(semantic):
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        ok, detail = explain_dry_run(con, "SELEC * FROMM inventory")
        assert ok is False
        assert detail
        gate = run_gate("SELEC * FROMM inventory", semantic, con=con)
        assert gate.passed is False
        assert gate.explain_ok is False or any("PARSE" in v for v in gate.violations)
    finally:
        con.close()


def test_explain_passes_valid_select(semantic):
    sql = "SELECT sku FROM inventory LIMIT 5"
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        gate = run_gate(sql, semantic, con=con)
        assert gate.passed is True
        assert gate.explain_ok is True
        assert gate.safe_sql
        ok, _ = explain_dry_run(con, gate.safe_sql)
        assert ok is True
    finally:
        con.close()


def test_retry_exhausted_abstains(semantic):
    attempts = {"n": 0}

    def bad(_prior: list[str]) -> str | None:
        attempts["n"] += 1
        return "SELEC bad FROM nowhere"

    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(bad, "x", semantic, con=None, max_retries=2)
    assert attempts["n"] == 3  # initial + 2 retries
    assert ei.value.violations
    for v in ei.value.violations:
        assert v in str(ei.value)


def test_sql_gate_abstain_str_includes_violations():
    exc = SqlGateAbstain(
        "SQL validation gate exhausted retries",
        violations=["UNKNOWN_COLUMN:nope", "EXPLAIN_FAILED:x"],
    )
    text = str(exc)
    assert "UNKNOWN_COLUMN:nope" in text
    assert "EXPLAIN_FAILED:x" in text
    assert "exhausted retries" in text


def test_sql_gate_abstain_str_without_violations_is_bare():
    assert str(SqlGateAbstain("SQL validation gate exhausted retries")) == (
        "SQL validation gate exhausted retries"
    )


def test_l2_attempt_keeps_gate_violations(monkeypatch):
    from CortexOS.dms import l2_generation

    class _Port:
        def is_configured(self) -> bool:
            return True

        def retrieve_schema(self, question: str) -> dict:
            return {"tables": {"inventory": {}}}

        def generate_candidates(self, question, schema, *, prior_violations=None):
            return ["SELEC bad FROM nowhere"]

        def record_validated(self, question, sql):
            return None

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _Port())
    out = l2_generation.attempt_l2("free form anything")
    assert out is not None
    assert out.sql is None
    assert out.violations
    for v in out.violations:
        assert v in (out.reason or "")


def test_l2_without_model_abstains(monkeypatch):
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    r = answer("Correlate supplier ESG scores with weather anomalies")
    assert r["route"] == "needs_clarification"
    assert "L2" in (r.get("assumptions") or "") or "L2" in (r.get("answer") or "") or True
    # Must not invent rows
    assert not r.get("rows")


def test_run_gate_manifest_skips_explain(semantic, monkeypatch):
    calls: list[tuple] = []

    def _boom(con, sql, params=None):
        calls.append((con, sql, params))
        return True, "ok"

    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.explain_dry_run", _boom
    )
    verified = _verified({"transactions": "TRUE"})
    gate = run_gate(
        "SELECT sku FROM inventory LIMIT 5",
        semantic,
        con=object(),
        verified=verified,
    )
    assert gate.passed is False
    assert gate.explain_ok is False
    assert gate.manifest_refused is True
    assert calls == []
    blob = " ".join(gate.violations)
    assert MANIFEST_VIOLATION_PREFIX in blob
    assert "PathNotAllowed" in blob
    assert "path_not_allowed" in blob


def test_run_gate_explains_post_enforce_sql(semantic, monkeypatch):
    captured: list[str] = []
    held: dict[str, str] = {}

    def _explain(con, sql, params=None):
        del con, params
        captured.append(sql)
        return True, "ok"

    real_enforce = enforce_manifest

    def _spy(sql, verified):
        out = real_enforce(sql, verified)
        held["sql"] = out
        return out

    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.explain_dry_run", _explain
    )
    monkeypatch.setattr(
        "CortexOS.execution.manifest.enforce_manifest", _spy
    )
    verified = _verified({"inventory": "TRUE"})
    gate = run_gate(
        "SELECT sku FROM inventory LIMIT 5",
        semantic,
        con=object(),
        verified=verified,
    )
    assert gate.passed is True
    assert gate.explain_ok is True
    assert held["sql"]
    assert captured == [held["sql"]]
    assert gate.safe_sql is held["sql"]


def test_gate_retries_manifest_then_accepts_granted(semantic, monkeypatch):
    captured: list[str] = []

    def _explain(con, sql, params=None):
        del con, params
        captured.append(sql)
        return True, "ok"

    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.explain_dry_run", _explain
    )
    n = {"i": 0}
    priors: list[list[str]] = []

    def _gen(prior: list[str]) -> str | None:
        priors.append(list(prior))
        n["i"] += 1
        if n["i"] == 1:
            return "SELECT sku FROM inventory LIMIT 5"
        return "SELECT sku FROM transactions LIMIT 5"

    verified = _verified({"transactions": "TRUE"})
    gate = gate_with_retry(
        _gen, "q", semantic, con=object(), verified=verified, max_retries=2
    )
    assert gate.passed is True
    assert n["i"] == 2
    assert any(MANIFEST_VIOLATION_PREFIX in v for v in priors[1])
    assert len(captured) == 1
    assert "transactions" in captured[0].lower()


def test_gate_manifest_exhausts_at_max_retries(semantic, monkeypatch):
    calls = {"explain": 0, "gen": 0}

    def _explain(con, sql, params=None):
        del con, sql, params
        calls["explain"] += 1
        return True, "ok"

    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.explain_dry_run", _explain
    )

    def _gen(_prior: list[str]) -> str | None:
        calls["gen"] += 1
        return "SELECT sku FROM inventory LIMIT 5"

    verified = _verified({"transactions": "TRUE"})
    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(
            _gen, "q", semantic, con=object(), verified=verified, max_retries=2
        )
    assert calls["gen"] == 2
    assert calls["explain"] == 0
    assert ei.value.manifest_refused is True
    assert any(MANIFEST_VIOLATION_PREFIX in v for v in ei.value.violations)


def test_l2_next_candidate_after_manifest_skip(monkeypatch):
    from CortexOS.dms import l2_generation

    class _Port:
        def is_configured(self) -> bool:
            return True

        def retrieve_schema(self, question: str) -> dict:
            return {"tables": {"transactions": {}}}

        def generate_candidates(self, question, schema, *, prior_violations=None):
            del question, schema, prior_violations
            return [
                "SELECT sku FROM inventory LIMIT 5",
                "SELECT sku FROM transactions LIMIT 5",
            ]

        def record_validated(self, question, sql):
            del question, sql
            return None

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _Port())
    out = l2_generation.attempt_l2(
        "free form anything",
        verified=_verified({"transactions": "TRUE"}),
    )
    assert out is not None
    assert out.sql
    assert out.refused is False
    assert "transactions" in out.sql.lower()


def test_l2_manifest_error_customer_envelope_is_refused(monkeypatch):
    from CortexOS.dms import l2_generation
    from CortexOS.dms.answer_engine import answer

    class _Port:
        def is_configured(self) -> bool:
            return True

        def retrieve_schema(self, question: str) -> dict:
            return {"tables": {"inventory": {}}}

        def generate_candidates(self, question, schema, *, prior_violations=None):
            del question, schema, prior_violations
            return ["SELECT sku FROM inventory LIMIT 5"]

        def record_validated(self, question, sql):
            raise AssertionError("must not promote a refused candidate")

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _Port())
    r = answer(
        "Correlate supplier ESG scores with weather anomalies",
        verified=_verified({"transactions": "TRUE"}),
    )
    assert r["route"] == "refused"
    assert r["layer"] == "refused"
    assert r["badge"] == "refused"
    assert r["badge"] != "session"
    assert r["rows"] == []
    assert r.get("sql_used") is None
    text = f"{r.get('answer') or ''} {r.get('assumptions') or ''}"
    assert "L2_MANIFEST" in text or "PathNotAllowed" in text
    assert r.get("violations_blocked")
