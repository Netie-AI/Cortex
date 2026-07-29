"""C3 — every hostile query in the corpus must die at the executor.

Not at the LLM, not in the UI, not by convention. The corpus in
``hostile_sql_corpus.json`` is replayed here in full; adding a case there adds a
test here. Cases are grouped by what the enforcer must do, and the
``allow_but_predicate_must_apply`` group additionally proves the predicate
reached every physical reference — being allowed through is not the same as
being filtered.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from CortexOS.execution.manifest import (
    ManifestError,
    PathNotAllowed,
    SqlNotAnalyzable,
    StatementNotAllowed,
    VerifiedManifest,
    enforce_manifest,
)
from packages.cortex_contract.execution import Manifest

CORPUS = json.loads((Path(__file__).parent / "hostile_sql_corpus.json").read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
SPEC = CORPUS["manifest"]
PREDICATES: dict[str, str] = {k.lower(): v for k, v in SPEC["row_predicates"].items()}

REFUSALS = {"reject_path", "reject_attach", "reject_predicate_bypass", "reject_statement"}


def _cases(*expected: str) -> list:
    return [pytest.param(c, id=c["name"]) for c in CASES if c["expected"] in expected]


@pytest.fixture(scope="module")
def verified() -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-1",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=SPEC["allowed_paths"],
        row_predicates=SPEC["row_predicates"],
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


# ── the corpus ───────────────────────────────────────────────────────────────


def test_corpus_is_large_enough_to_mean_something() -> None:
    """C3 asks for at least 25 hostile cases; drifting below that is a regression."""
    hostile = [c for c in CASES if c["expected"] in REFUSALS]
    assert len(hostile) >= 25, f"only {len(hostile)} hostile cases"
    assert len({c["name"] for c in CASES}) == len(CASES), "duplicate case names"


@pytest.mark.parametrize("case", _cases(*REFUSALS))
def test_hostile_case_is_refused(case: dict, verified: VerifiedManifest) -> None:
    with pytest.raises(ManifestError) as caught:
        enforce_manifest(case["sql"], verified)
    # The refusal must be typed, so DMS can log a security event rather than a retry.
    assert isinstance(
        caught.value, (PathNotAllowed, StatementNotAllowed, SqlNotAnalyzable)
    ), type(caught.value)
    assert caught.value.code != "manifest_error"


@pytest.mark.parametrize("case", _cases("allow_but_predicate_must_apply"))
def test_permitted_case_survives_with_its_predicate(
    case: dict, verified: VerifiedManifest
) -> None:
    out = enforce_manifest(case["sql"], verified)
    for table, where in _unguarded_references(out):
        pytest.fail(f"{table} is not filtered; nearest WHERE was {where!r}\n{out}")


@pytest.mark.parametrize("case", _cases("minting_invariant"))
def test_documented_minting_gap_is_not_silently_claimed_as_enforced(
    case: dict, verified: VerifiedManifest
) -> None:
    """This case passes Cortex by design; the manifest was mis-minted upstream.

    It is asserted rather than deleted so nobody later reads the corpus as proof
    that Cortex closes it. The fix belongs in DMS's minting (T2) — see the
    case's enforcer_note.
    """
    assert case.get("enforcer_note"), "a documented gap must carry its rationale"
    enforce_manifest(case["sql"], verified)  # does not raise, and that is the point


def _unguarded_references(sql: str) -> list[tuple[str, str]]:
    """Physical references to a predicated table that no enclosing WHERE filters."""
    root = sqlglot.parse_one(sql, read="duckdb")
    unguarded: list[tuple[str, str]] = []
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue
        predicate = PREDICATES.get(table.name.lower())
        if predicate is None:
            continue
        select = table.find_ancestor(exp.Select)
        where = select.args.get("where") if select else None
        rendered = where.sql(dialect="duckdb") if where else ""
        column = predicate.split("=")[0].strip()
        if column not in rendered:
            unguarded.append((table.sql(dialect="duckdb"), rendered))
    return unguarded


# ── the specific escapes C3 names ────────────────────────────────────────────
# Named explicitly so a corpus edit cannot quietly drop one of them.


@pytest.mark.parametrize(
    "technique",
    ["nested_cte", "read_parquet_subquery", "union_out_of_manifest", "attach", "lateral_join"],
)
def test_named_technique_is_covered(technique: str) -> None:
    matched = [c for c in CASES if technique in c["technique"] or technique in c["name"]]
    assert matched, f"C3 names {technique} but the corpus has no case for it"


def test_predicate_stripping_is_covered() -> None:
    stripping = [c for c in CASES if "predicate" in c["technique"] or "1=1" in c["sql"]]
    assert stripping, "no OR 1=1 / predicate-stripping case"


# ── properties, stated directly ──────────────────────────────────────────────


def test_stacked_statements_are_refused(verified: VerifiedManifest) -> None:
    """sqlglot 30.11 wraps these in a Block instead of raising, so this is easy to miss."""
    with pytest.raises(StatementNotAllowed):
        enforce_manifest("SELECT id FROM orders; DROP TABLE orders", verified)


def test_unparseable_statement_is_refused(verified: VerifiedManifest) -> None:
    with pytest.raises(SqlNotAnalyzable):
        enforce_manifest("SELECT * FROM 'unterminated", verified)


def test_opaque_command_is_refused(verified: VerifiedManifest) -> None:
    """PREPARE/EXECUTE/LOAD parse to exp.Command with the body as an opaque string."""
    with pytest.raises(SqlNotAnalyzable):
        enforce_manifest("PREPARE p AS SELECT * FROM secrets", verified)


def test_deeply_nested_sql_does_not_crash_the_process(verified: VerifiedManifest) -> None:
    """RecursionError is not a SqlglotError; an uncaught one is an availability bug."""
    with pytest.raises(ManifestError):
        enforce_manifest("SELECT " + "ABS(" * 200 + "1" + ")" * 200, verified)


def test_oversized_sql_is_refused_before_parsing(verified: VerifiedManifest) -> None:
    with pytest.raises(SqlNotAnalyzable):
        enforce_manifest("SELECT 1 -- " + "x" * 200_000, verified)


def test_glob_star_does_not_cross_a_directory_separator(verified: VerifiedManifest) -> None:
    """fnmatch would allow this; a manifest granting one directory means one directory."""
    with pytest.raises(PathNotAllowed):
        enforce_manifest(
            "SELECT * FROM read_parquet('/data/pool/tenant_a/nested/deep.parquet')", verified
        )


def test_sibling_directory_sharing_a_prefix_is_refused(verified: VerifiedManifest) -> None:
    """The classic startswith() bug: tenant_a is not a prefix grant over tenant_a_evil."""
    with pytest.raises(PathNotAllowed):
        enforce_manifest(
            "SELECT * FROM read_parquet('/data/pool/tenant_a_evil/x.parquet')", verified
        )


def test_alias_survives_predicate_injection(verified: VerifiedManifest) -> None:
    """Losing the alias emits SQL that references an unbound name — broken, not safe."""
    out = enforce_manifest(
        "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id", verified
    )
    assert " AS o" in out and " AS u" in out
    sqlglot.parse_one(out, read="duckdb")  # still valid SQL


def test_unaliased_table_keeps_its_name_bindable(verified: VerifiedManifest) -> None:
    out = enforce_manifest("SELECT orders.id FROM orders", verified)
    assert "AS orders" in out


def test_enforcement_requires_a_verified_manifest() -> None:
    """The signature is the type: you cannot enforce against something unverified."""
    import inspect

    annotation = inspect.signature(enforce_manifest).parameters["verified"].annotation
    assert "VerifiedManifest" in str(annotation)
