"""VALUE-NORMALIZE-CX-01 (#387): stored SQL on a declared column pins only canonical values.

PR #385's failure mode, caught statically: certified or metric SQL that writes
``txn_type = 'OUT'`` returns nothing on a lake that stores the declared
canonical ``outbound``, and nothing raises. Every comparison of a string with
``transactions.txn_type`` in the L0 serve set, in the metrics that compute a
declared measure, and in ``query_service._sales_sql`` must name a value the
ontology declares as canonical. The check reads parsed SQL against the
declaration; it holds no value of its own.

The rows those queries return are asserted in
``tests/test_crew/test_certified_txn_type_rows.py`` and
``tests/dms/test_value_domain.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot
import yaml
from sqlglot import exp

from CortexOS.dms import query_service
from CortexOS.ontology import value_domain as vd
from packs.dms.semantic import loader

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "packs" / "dms"
CERTIFIED = PACK / "semantic" / "certified_queries.yaml"
DOMAINS = vd.load_value_domains(PACK)


def _declared_literals(sql: str) -> list[tuple[str, str]]:
    """``(table.column, literal)`` for each string compared with a declared column."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    by_col = {d.column.lower(): d for d in DOMAINS}
    out: list[tuple[str, str]] = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In):
        if isinstance(node, exp.In):
            col, lits = node.this, list(node.expressions)
        elif isinstance(node.left, exp.Column):
            col, lits = node.left, [node.right]
        else:
            col, lits = node.right, [node.left]
        dom = by_col.get(col.name.lower()) if isinstance(col, exp.Column) else None
        if dom is None:
            continue
        out += [(dom.key, str(lit.this)) for lit in lits if isinstance(lit, exp.Literal) and lit.is_string]
    return out


def _violations(sql: str) -> list[str]:
    canonical = {d.key: set(d.canonical) for d in DOMAINS}
    return [f"{key}={lit!r}" for key, lit in _declared_literals(sql) if lit not in canonical[key]]


def _stored_sql() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for row in yaml.safe_load(CERTIFIED.read_text(encoding="utf-8"))["certified"]:
        out.append((f"certified:{row['id']}", str(row["sql"])))
    model = loader.load_all()
    for mid, metric in model.metrics.items():
        if "txn_type" not in metric.sql:
            continue
        sample = {p: loader._sample_value(spec) for p, spec in metric.params.items()}
        out.append((f"metric:{mid}", loader.compile_metric(model, mid, sample)))
    for q in ("top 5 sales by revenue", "top 5 sales by quantity"):
        out.append((f"query_service:_sales_sql:{q}", query_service._sales_sql(q)[0]))
    return out


STORED = _stored_sql()


def test_the_lint_is_not_vacuous() -> None:
    pinned = [sid for sid, sql in STORED if _declared_literals(sql)]
    assert {
        "certified:cq_sales_top5_value",
        "certified:cq_top3_category_sales",
        "metric:sales_by_value",
        "metric:revenue_total",
    } <= set(pinned)
    assert any(s.startswith("query_service:") for s in pinned)


@pytest.mark.parametrize(("sid", "sql"), STORED, ids=[s for s, _ in STORED])
def test_stored_sql_pins_only_declared_canonical_values(sid: str, sql: str) -> None:
    assert _violations(sql) == [], sid
    # The gold rewrite has nothing to do here either: no source spelling remains.
    assert vd.canonicalize_sql(sql, DOMAINS) == sql, sid


@pytest.mark.parametrize("bad", ["OUT", "Outbound", "sale"])
def test_a_source_or_undeclared_spelling_in_stored_sql_is_named(bad: str) -> None:
    """Must-fail: the #385 regression (and an undeclared spelling) is caught by name."""
    sql = next(s for sid, s in STORED if sid == "certified:cq_sales_top5_value")
    assert "'outbound'" in sql
    broken = sql.replace("'outbound'", f"'{bad}'")
    assert _violations(broken) == [f"transactions.txn_type={bad!r}"]
