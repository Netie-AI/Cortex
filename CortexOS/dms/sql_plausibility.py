"""C7 plausibility — prove a filter can select something before the answer ships.

The gate below this one (:mod:`CortexOS.dms.sql_validate_gate`) proves the SQL
parses and that DuckDB will plan it. Neither proves the query *means* anything:
``WHERE sku = 'BETA'`` parses, plans, and returns zero rows against a warehouse
that spells it ``SKU-BETA``. The customer then reads a confident "0" that is
simply wrong — the value-encoding class (A-0003), and the reason CLAUDE.md §8
ends the C7 pipeline with plausibility rather than with EXPLAIN.

So the last stage before an answer is empirical. For every string literal the
query filters on, ask the warehouse whether that value exists in that column at
all. A literal that matches nothing is a broken filter, not an empty result,
and the answer path must abstain with the near-misses instead of stamping
success.

Deliberately narrow, because a control that refuses legitimate work is a
failure too (R-0005):

* Only **string** literals compared against a column. Numbers and dates are not
  the encoding-mismatch class and legitimately match nothing all the time.
* The probe asks "does this VALUE exist in this COLUMN", never "does the whole
  query return rows". ``0 delayed shipments`` is a true answer and stays one;
  only an impossible *predicate* abstains.
* Anything under an ``OR`` is skipped — a branch that matches nothing does not
  make the disjunction wrong.
* Bounded: at most :data:`MAX_PROBES` literals per query, ``LIMIT 1`` each.
* Unprobeable (no runner, ambiguous table, warehouse error) reports ``ok`` with
  a ``skipped_reason`` the caller must surface. A check that quietly did not run
  is the failure mode this module exists to prevent, so it never reports clean.

Engine-side on purpose. The pack already normalises literals on the way out
(``packs/dms/generative/literal_normalize.py``), but the engine may not import a
pack and must not take a pack's word for it — the port contract is that a pack
proposes SQL and only the engine decides whether it may run. Probing the
warehouse needs neither the pack's value dictionary nor ``duckdb``: the caller
injects a runner, so this module stays inside both boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import sqlglot
from sqlglot import exp

# A question filtering on more than a handful of entity literals is not the
# shape this catches; the cap keeps a pathological query from fanning out.
MAX_PROBES = 4
MAX_CANDIDATES = 5

#: Layers whose SQL is probed. ``governed_metric`` is compiled from the semantic
#: layer with values already resolved, so probing it would add latency to the hot
#: path and risk refusing legitimate work. Generated SQL comes from a model, and
#: query-skill SQL is a *replay* of a capture that may have gone stale — both can
#: carry a literal the warehouse has never held.
PROBED_LAYERS = frozenset({"generated", "query_skill"})

#: ``(probe_sql) -> rows``. Injected so the caller decides whether the probe runs
#: through ``execute_sql`` (manifest enforced, contract path) or a raw read-only
#: connection (legacy path).
Runner = Callable[[str], list[dict[str, Any]]]


@dataclass(slots=True, frozen=True)
class LiteralPredicate:
    """One ``column <op> 'literal'`` comparison lifted out of the statement."""

    table: str
    column: str
    value: str

    def describe(self) -> str:
        return f"{self.column} = {self.value!r}"


@dataclass(slots=True)
class ImpossiblePredicate:
    predicate: LiteralPredicate
    candidates: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlausibilityResult:
    """Outcome of the probe pass.

    ``ok`` False means at least one filter cannot match any row and the caller
    must abstain. ``ok`` True with a ``skipped_reason`` means the check did not
    actually run — honest, and the caller is expected to say so in the answer's
    assumptions rather than imply the query was proven plausible.
    """

    ok: bool
    probed: int = 0
    impossible: list[ImpossiblePredicate] = field(default_factory=list)
    skipped_reason: str | None = None

    def reason(self) -> str:
        """Customer-facing explanation of the first impossible filter."""
        if not self.impossible:
            return ""
        first = self.impossible[0]
        base = (
            f"no {first.predicate.column} in {first.predicate.table} "
            f"equals {first.predicate.value!r}"
        )
        if first.candidates:
            shown = ", ".join(repr(c) for c in first.candidates[:3])
            return f"{base} (closest: {shown})"
        return base


def _alias_map(stmt: exp.Expression) -> tuple[dict[str, str], list[str]]:
    """Map alias -> real table name, plus the list of distinct tables in scope."""
    aliases: dict[str, str] = {}
    tables: list[str] = []
    for table in stmt.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        if name not in tables:
            tables.append(name)
        aliases[name] = name
        alias = table.alias
        if alias:
            aliases[str(alias)] = name
    return aliases, tables


def _resolve_table(col: exp.Column, aliases: dict[str, str], tables: list[str]) -> str | None:
    """Which table this column belongs to, or None when it cannot be pinned down.

    Unqualified columns are only resolvable when exactly one table is in scope.
    Guessing across a join would probe the wrong table and could abstain on a
    perfectly good query, so ambiguity skips instead.
    """
    qualifier = col.table
    if qualifier:
        return aliases.get(str(qualifier))
    if len(tables) == 1:
        return tables[0]
    return None


def _string_value(node: exp.Expression | None) -> str | None:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _under_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def extract_literal_predicates(sql: str) -> list[LiteralPredicate]:
    """Lift ``column = 'x'`` / ``column IN ('x', …)`` comparisons out of ``sql``.

    Polarity is deliberately ignored. ``NOT IN ('BETA')`` is the same defect as
    ``= 'BETA'`` when the warehouse has no such value: the exclusion silently
    does nothing and the customer gets an unfiltered ranking stamped as filtered.
    Both ask the same question — is this a real value of this column?
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception:  # noqa: BLE001 — unparseable SQL is the gate's problem, not ours
        return []
    if not statements or statements[0] is None:
        return []
    stmt = statements[0]

    aliases, tables = _alias_map(stmt)
    found: list[LiteralPredicate] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(col: exp.Column, raw: str, node: exp.Expression) -> None:
        if _under_or(node):
            return
        table = _resolve_table(col, aliases, tables)
        if not table or not col.name:
            return
        key = (table, col.name, raw)
        if key in seen:
            return
        seen.add(key)
        found.append(LiteralPredicate(table=table, column=col.name, value=raw))

    for node in stmt.walk():
        if isinstance(node, (exp.EQ, exp.NEQ)):
            left, right = node.this, node.expression
            if isinstance(left, exp.Column) and (raw := _string_value(right)) is not None:
                _add(left, raw, node)
            elif isinstance(right, exp.Column) and (raw := _string_value(left)) is not None:
                _add(right, raw, node)
        elif isinstance(node, exp.In):
            col = node.this
            if not isinstance(col, exp.Column):
                continue
            for item in node.expressions or []:
                raw = _string_value(item)
                if raw is not None:
                    _add(col, raw, node)

    return found[:MAX_PROBES]


def _probe_sql(pred: LiteralPredicate) -> str:
    """``SELECT col FROM t WHERE col = 'v' LIMIT 1`` — built as an AST, not a string.

    Rendering through sqlglot means the literal is re-escaped by the same code
    that parsed it, so a value carrying a quote cannot re-open the statement.

    Selects the column rather than a bare ``1`` because the probe may be run
    through the same manifest enforcement as a real query, and a projection of
    an allowlisted column survives that where a literal projection may not.
    """
    query = (
        exp.select(exp.column(pred.column))
        .from_(exp.to_table(pred.table))
        .where(exp.EQ(this=exp.column(pred.column), expression=exp.Literal.string(pred.value)))
        .limit(1)
    )
    return query.sql(dialect="duckdb")


def _candidates_sql(pred: LiteralPredicate) -> str:
    """Near-miss values, so the abstain can say "did you mean SKU-BETA"."""
    lowered = exp.Lower(this=exp.cast(exp.column(pred.column), "VARCHAR"))
    needle = pred.value.lower().replace("%", "").replace("_", " ").strip()
    query = (
        exp.select(exp.column(pred.column))
        .distinct()
        .from_(exp.to_table(pred.table))
        .where(exp.Like(this=lowered, expression=exp.Literal.string(f"%{needle}%")))
        .limit(MAX_CANDIDATES)
    )
    return query.sql(dialect="duckdb")


def check(sql: str, runner: Runner | None, *, layer: str = "generated") -> PlausibilityResult:
    """Probe every entity literal in ``sql``. ``ok`` False means the caller abstains.

    Never raises: a warehouse that refuses the probe degrades to
    ``ok=True, skipped_reason=…`` so a probe failure cannot take down an answer
    that the real gates already passed. The caller is required to surface that
    reason — see :data:`PROBED_LAYERS` and the answer engine's assumptions line.
    """
    if layer not in PROBED_LAYERS:
        return PlausibilityResult(ok=True, skipped_reason=f"layer {layer} not probed")
    if runner is None:
        return PlausibilityResult(ok=True, skipped_reason="no warehouse runner available")

    preds = extract_literal_predicates(sql)
    if not preds:
        return PlausibilityResult(ok=True, probed=0)

    impossible: list[ImpossiblePredicate] = []
    probed = 0
    for pred in preds:
        try:
            rows = runner(_probe_sql(pred))
        except Exception as exc:  # noqa: BLE001
            return PlausibilityResult(
                ok=True,
                probed=probed,
                impossible=impossible,
                skipped_reason=f"probe failed: {str(exc)[:160]}",
            )
        probed += 1
        if rows:
            continue
        candidates: list[str] = []
        try:
            for row in runner(_candidates_sql(pred)) or []:
                for value in row.values():
                    if value is not None:
                        candidates.append(str(value))
        except Exception:  # noqa: BLE001 — suggestions are a nicety, absence is fine
            candidates = []
        impossible.append(ImpossiblePredicate(predicate=pred, candidates=candidates))

    return PlausibilityResult(ok=not impossible, probed=probed, impossible=impossible)


__all__ = [
    "MAX_CANDIDATES",
    "MAX_PROBES",
    "PROBED_LAYERS",
    "ImpossiblePredicate",
    "LiteralPredicate",
    "PlausibilityResult",
    "Runner",
    "check",
    "extract_literal_predicates",
]
