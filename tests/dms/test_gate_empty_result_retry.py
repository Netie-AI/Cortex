"""CX-SC-01 — L2 self-correction: an empty result is a gate violation.

A candidate that passes allowlist → manifest → EXPLAIN is probed once. Zero
rows or an execution error is fed back to the generator (``EMPTY_RESULT`` /
``EXECUTION_ERROR:``) and retried within ``max_retries``; exhaustion abstains
instead of serving an empty success (CLAUDE.md §8). Answer-path cases assert
the rendered answer text and the returned rows, not only SQL.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest

from CortexOS.dms import l2_generation
from CortexOS.dms.sql_validate_gate import (
    EMPTY_RESULT_VIOLATION,
    EXECUTION_ERROR_PREFIX,
    MANIFEST_VIOLATION_PREFIX,
    ProbeResult,
    SqlGateAbstain,
    gate_with_retry,
)
from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection, load_semantic_layer
from CortexOS.execution.manifest import VerifiedManifest

QUESTION = "what is the stock quantity for the beta sku in inventory"
EMPTY_SQL = "SELECT sku, sku_name, quantity_kg FROM inventory WHERE sku = 'BETA'"
GOOD_SQL = "SELECT sku, sku_name, quantity_kg FROM inventory WHERE sku = 'SKU-BETA'"
# Binds and EXPLAINs fine; the cast only fails when rows are filtered. It sits
# in WHERE because the count(*) probe lets DuckDB prune unused projections.
ERROR_SQL = "SELECT sku, quantity_kg FROM inventory WHERE CAST(sku_name AS INTEGER) > 0"


def _verified(predicates: dict[str, str]) -> VerifiedManifest:
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="cx-sc-01",
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


class _ScriptedPort:
    """Fake L2 port: one scripted candidate list per generate_candidates call."""

    def __init__(self, script: list[list[str]]) -> None:
        self.script = list(script)
        self.priors: list[list[str]] = []

    def is_configured(self) -> bool:
        return True

    def retrieve_schema(self, question: str) -> dict:
        return {"tables": {"inventory": {}}}

    def generate_candidates(self, question, schema, *, prior_violations=None):
        self.priors.append(list(prior_violations or []))
        if not self.script:
            return []
        return self.script.pop(0)

    def record_validated(self, question, sql):
        return None


@pytest.fixture(scope="module")
def semantic():
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    return load_semantic_layer()


@pytest.fixture
def l2_only(monkeypatch, semantic):
    """Route every question straight to L2 (same isolation as the FreeRoute envelope test)."""
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._space_doc_rag", lambda: None, raising=False)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr("packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False)

    def _install(script: list[list[str]]) -> _ScriptedPort:
        port = _ScriptedPort(script)
        monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)
        return port

    return _install


def _direct(sql: str) -> list[dict[str, Any]]:
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _answer() -> dict[str, Any]:
    from CortexOS.dms.answer_engine import answer

    return answer(QUESTION)


# -- answer path: rendered text + rows ------------------------------------------


def test_empty_candidate_retried_and_second_is_served(l2_only) -> None:
    port = l2_only([[EMPTY_SQL], [GOOD_SQL]])
    env = _answer()

    assert env["badge"] == "L2_VALIDATED", env
    expected = _direct(GOOD_SQL)
    assert expected, "fixture must hold SKU-BETA"
    assert env["rows"], env
    assert env["rows"] == expected
    text = env.get("answer") or ""
    assert "SKU-BETA" in text or "Beta Trial Pack" in text, text

    assert len(port.priors) == 2
    assert port.priors[0] == []
    assert port.priors[1], port.priors
    assert port.priors[1][0].startswith("EMPTY_RESULT")
    assert "SKU-BETA" in (env.get("sql_used") or "")


def test_every_candidate_empty_abstains_with_no_rows(l2_only) -> None:
    port = l2_only([[EMPTY_SQL], [EMPTY_SQL], [EMPTY_SQL]])
    env = _answer()

    assert env["badge"] == "abstain", env
    assert env["rows"] == []
    assert env.get("chart_spec") is None
    assert env.get("sql_used") is None
    blob = f"{env.get('answer') or ''} {env.get('assumptions') or ''}"
    assert "no rows matched" in blob, blob
    assert len(port.priors) == 3
    assert all(p and p[0].startswith("EMPTY_RESULT") for p in port.priors[1:])


def test_execution_error_is_fed_back_then_recovers(l2_only) -> None:
    port = l2_only([[ERROR_SQL], [GOOD_SQL]])
    env = _answer()

    assert env["badge"] == "L2_VALIDATED", env
    assert env["rows"] == _direct(GOOD_SQL)
    text = env.get("answer") or ""
    assert "SKU-BETA" in text or "Beta Trial Pack" in text, text
    assert port.priors[1][0].startswith(EXECUTION_ERROR_PREFIX), port.priors


def test_attempt_l2_empty_exhaustion_reason(l2_only) -> None:
    l2_only([[EMPTY_SQL], [EMPTY_SQL], [EMPTY_SQL]])
    out = l2_generation.attempt_l2(QUESTION, promote=False)
    assert out is not None
    assert out.sql is None
    assert out.reason.startswith("no rows matched after 3 attempts")
    assert EMPTY_RESULT_VIOLATION in out.reason
    assert out.violations == [EMPTY_RESULT_VIOLATION]
    assert out.probe_rows == 0


# -- gate level -----------------------------------------------------------------


def test_gate_feeds_execution_error_back(semantic) -> None:
    priors: list[list[str]] = []
    sqls = iter(["SELECT sku FROM inventory LIMIT 5", "SELECT sku FROM inventory LIMIT 3"])

    def _gen(prior: list[str]) -> str | None:
        priors.append(list(prior))
        return next(sqls)

    results = iter([ProbeResult(row_count=0, error="Conversion Error: boom"), ProbeResult(row_count=3)])
    gate = gate_with_retry(
        _gen, "q", semantic, con=None, max_retries=2, probe=lambda _s: next(results)
    )
    assert gate.passed is True
    assert "LIMIT 3" in (gate.safe_sql or "")
    assert priors[1] == [f"{EXECUTION_ERROR_PREFIX} Conversion Error: boom"]


def test_gate_probe_exhaustion_sets_empty_result(semantic) -> None:
    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(
            lambda _p: "SELECT sku FROM inventory LIMIT 5",
            "q",
            semantic,
            con=None,
            max_retries=2,
            probe=lambda _s: ProbeResult(row_count=0),
        )
    assert ei.value.empty_result is True
    assert ei.value.attempts == 3
    assert ei.value.violations == [EMPTY_RESULT_VIOLATION]


def test_abstain_empty_result_defaults_false() -> None:
    assert SqlGateAbstain("x").empty_result is False


def test_manifest_refused_candidate_never_reaches_probe(semantic, monkeypatch) -> None:
    monkeypatch.setattr(
        "CortexOS.dms.sql_validate_gate.explain_dry_run", lambda con, sql, params=None: (True, "ok")
    )
    probed: list[str] = []

    def _probe(sql: str) -> ProbeResult:
        probed.append(sql)
        return ProbeResult(row_count=1)

    verified = _verified({"transactions": "TRUE"})
    with pytest.raises(SqlGateAbstain) as ei:
        gate_with_retry(
            lambda _p: "SELECT sku FROM inventory LIMIT 5",
            "q",
            semantic,
            con=object(),
            verified=verified,
            max_retries=2,
            probe=_probe,
        )
    assert probed == []
    assert ei.value.manifest_refused is True
    assert ei.value.empty_result is False
    assert any(MANIFEST_VIOLATION_PREFIX in v for v in ei.value.violations)

    # A later granted candidate is probed exactly once, on post-enforce SQL.
    n = {"i": 0}

    def _gen(_prior: list[str]) -> str | None:
        n["i"] += 1
        return "SELECT sku FROM inventory LIMIT 5" if n["i"] == 1 else "SELECT sku FROM transactions LIMIT 5"

    gate = gate_with_retry(
        _gen, "q", semantic, con=object(), verified=verified, max_retries=2, probe=_probe
    )
    assert gate.passed is True
    assert len(probed) == 1
    assert probed[0] == gate.safe_sql
    assert "transactions" in probed[0].lower()


def test_attempt_l2_manifest_refusal_skips_probe(l2_only, monkeypatch) -> None:
    from CortexOS.dms import sql_validate_gate

    real = sql_validate_gate._probe_violation
    calls = {"n": 0}

    def _spy(probe, sql):
        calls["n"] += 1
        return real(probe, sql)

    monkeypatch.setattr(sql_validate_gate, "_probe_violation", _spy)
    l2_only([[GOOD_SQL], [GOOD_SQL], [GOOD_SQL]])
    out = l2_generation.attempt_l2(
        QUESTION, verified=_verified({"transactions": "TRUE"}), promote=False
    )
    assert out is not None
    assert out.refused is True
    assert out.sql is None
    assert calls["n"] == 0


def test_shadow_records_probe_rows(l2_only, monkeypatch, tmp_path: Path) -> None:
    l2_only([[GOOD_SQL]])
    path = tmp_path / "l2_shadow.jsonl"
    monkeypatch.setenv("DMS_L2_SHADOW", "1")
    monkeypatch.setenv("DMS_L2_SHADOW_PATH", str(path))
    l2_generation.maybe_record_l2_shadow(
        QUESTION, {"layer": "governed_metric", "rows": [{"a": 1}], "badge": "governed_metric"}
    )
    rec = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "probe_rows" in rec
    assert rec["probe_rows"] == len(_direct(GOOD_SQL))
