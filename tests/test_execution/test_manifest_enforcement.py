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


# ── red-team regressions ─────────────────────────────────────────────────────
# Every one of these was a working escape found by adversarial review after the
# 90-case corpus already passed. They are kept as named tests, not folded into
# the corpus, so the specific mistake stays legible.


@pytest.mark.parametrize(
    "sql",
    [
        # A FROM-clause alias rebinding a governed name made the real table
        # invisible to both the predicate injector and the grant check.
        "SELECT o.* FROM unnest([1]) AS orders(x), orders AS o",
        "SELECT o.* FROM (VALUES (1)) AS orders(x), orders AS o",
        "SELECT * FROM orders o, LATERAL (SELECT 1) AS users",
    ],
    ids=["unnest_rebind", "values_rebind", "lateral_rebind"],
)
def test_local_binding_cannot_rebind_a_governed_name(
    sql: str, verified: VerifiedManifest
) -> None:
    with pytest.raises(PathNotAllowed):
        enforce_manifest(sql, verified)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT s.* FROM unnest([1]) AS secrets(x), secrets AS s",
        "SELECT s.* FROM (VALUES (1)) AS secrets(x), secrets AS s",
    ],
    ids=["unnest_exempt", "values_exempt"],
)
def test_an_alias_cannot_exempt_a_table_from_the_grant_check(
    sql: str, verified: VerifiedManifest
) -> None:
    """A CTE shadows a base table; a FROM-clause alias does not.

    Treating both as "locally bound" let an attacker exempt any table from the
    grant check by aliasing something else to its name — the second entry still
    resolves to the base table.
    """
    with pytest.raises(PathNotAllowed):
        enforce_manifest(sql, verified)


@pytest.mark.parametrize(
    "function",
    [
        "parquet_metadata",
        "parquet_schema",
        "parquet_file_metadata",
        "read_json_objects_auto",
        "read_ndjson_objects",
        "iceberg_metadata",
        "duckdb_settings",
    ],
)
def test_unrecognised_table_functions_are_refused(
    function: str, verified: VerifiedManifest
) -> None:
    """DuckDB has more file readers than any enumeration will hold.

    Listing the dangerous ones is a denylist: an unlisted reader had its path
    argument collected by nothing and checked by nothing. Unknown FROM-position
    functions are now refused, which turns the list back into an allowlist.
    """
    with pytest.raises(SqlNotAnalyzable):
        enforce_manifest(f"SELECT * FROM {function}('/etc/secrets.parquet')", verified)


def test_known_readers_still_work_inside_the_manifest(verified: VerifiedManifest) -> None:
    """The allowlist flip must not break the readers a session is entitled to."""
    out = enforce_manifest(
        "SELECT * FROM read_parquet('/data/pool/tenant_a/x.parquet')", verified
    )
    assert "READ_PARQUET" in out.upper()


def test_cte_still_shadows_legitimately(verified: VerifiedManifest) -> None:
    """The narrowing must not break ordinary WITH usage."""
    out = enforce_manifest(
        "WITH recent AS (SELECT * FROM orders WHERE amount > 1) SELECT * FROM recent", verified
    )
    assert "tenant_id" in out


# ── false positives the hardening introduced, and their fixes ────────────────
# Deny-by-default is only correct if it denies the right things. These were
# found by the same adversarial review, attacking usability rather than
# security: a control that refuses ordinary analytics gets switched off.


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/data/pool/tenant_a/e.csv', delim=';', header=true)",
        "SELECT * FROM read_csv('/data/pool/tenant_a/e.csv', columns={'id':'INTEGER'})",
        "SELECT * FROM read_csv('/data/pool/tenant_a/e.csv', compression='gzip')",
        "SELECT * FROM read_json('/data/pool/tenant_a/e.json', format='newline_delimited')",
    ],
    ids=["delim", "columns", "compression", "json_format"],
)
def test_reader_options_are_not_checked_as_paths(sql: str) -> None:
    """delim=';' is configuration, not a file.

    Sweeping every string literal in the call caught option values too, so a
    semicolon-delimited CSV sitting squarely inside the grant was refused — and
    the error blamed the manifest for it.
    """
    manifest = Manifest(
        session_id="s", org_id="acme", pool_id="p", issuer_key_id="int-1",
        allowed_paths=["/data/pool/tenant_a/*.csv", "/data/pool/tenant_a/*.json"],
        row_predicates={"orders": "tenant_id = 'a'"},
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        signature="x",
    )
    verified = VerifiedManifest(manifest=manifest, issuer_kid="int-1",
                                verified_at=datetime.now(timezone.utc))
    enforce_manifest(sql, verified)


def test_an_option_value_cannot_smuggle_a_path(verified: VerifiedManifest) -> None:
    """Skipping option values must not skip the path itself."""
    with pytest.raises(PathNotAllowed):
        enforce_manifest("SELECT * FROM read_csv('/etc/passwd', delim=';')", verified)


def test_a_computed_path_is_refused_not_ignored(verified: VerifiedManifest) -> None:
    """An argument the enforcer cannot resolve is refused, not assumed harmless."""
    with pytest.raises(PathNotAllowed):
        enforce_manifest(
            "SELECT * FROM read_parquet('/data/pool/tenant_a/x' || '/../../etc/p.parquet')",
            verified,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM generate_series(1, 10)",
        "SELECT * FROM range(5)",
        "SELECT d FROM generate_series(DATE '2024-01-01', DATE '2024-03-01', INTERVAL 1 MONTH) t(d)",
    ],
    ids=["generate_series", "range", "date_spine"],
)
def test_row_generators_are_allowed(sql: str, verified: VerifiedManifest) -> None:
    """A date spine touches no storage; refusing it buys nothing and costs a lot."""
    enforce_manifest(sql, verified)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.* FROM orders o QUALIFY row_number() OVER (PARTITION BY o.uid ORDER BY o.amount DESC) = 1",
        "SELECT region, sum(amount) FROM orders GROUP BY ROLLUP(region)",
        "SELECT * FROM orders WHERE amount > (SELECT avg(amount) FROM orders)",
    ],
    ids=["qualify", "rollup", "correlated_aggregate"],
)
def test_ordinary_analytics_still_passes(sql: str, verified: VerifiedManifest) -> None:
    out = enforce_manifest(sql, verified)
    assert "tenant_id" in out


def test_metadata_functions_stay_refused(verified: VerifiedManifest) -> None:
    """Allowing generators must not have opened the engine-introspection door."""
    with pytest.raises(SqlNotAnalyzable):
        enforce_manifest("SELECT * FROM duckdb_settings()", verified)


def test_schema_qualified_column_is_refused_clearly(verified: VerifiedManifest) -> None:
    """Wrapping makes main.orders.uid unresolvable; say so here, not via a binder error."""
    with pytest.raises(SqlNotAnalyzable) as caught:
        enforce_manifest("SELECT main.orders.uid FROM main.orders", verified)
    assert "orders.uid" in str(caught.value)


def test_plain_qualified_column_still_works(verified: VerifiedManifest) -> None:
    out = enforce_manifest("SELECT orders.uid FROM main.orders", verified)
    assert "AS orders" in out
