"""The installed DuckDB honours LIMIT on every shape a served answer can take.

DuckDB 2.0.0 drops a LIMIT when a LEFT JOIN's ON carries a constant filter the
optimizer proves empty: the plan shows ``Empty Result`` under the join and the
LIMIT operator is gone. It drops it at the top level, on a UNION, in a wrapped
subquery, in a CTE and under ``count(*)`` and ``IN``, so a cap at the fetch
cannot repair it. Through ``POST /v1/contract/ask`` a reading ending
``LIMIT 5`` served 5001 rows, past the gate's ``MAX_LIMIT`` too.

``pyproject.toml`` pins ``duckdb>=1.1,<2`` (``uv.lock`` and the image locks
already resolve 1.5.5). These run on the real engine and go red on 2.0.0, so a
pin lift has to show a DuckDB that keeps every LIMIT below.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

# b.v is never 50, so the optimizer proves the join side empty.
DROPPED = "SELECT a.id FROM a LEFT JOIN b ON a.k = b.k AND b.v = 50"


@pytest.fixture
def lake(tmp_path: Path) -> Path:
    path = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE a AS SELECT range AS id, range % 3 AS k FROM range(100)")
        con.execute("CREATE TABLE b AS SELECT range AS k, range * 1.0 AS v FROM range(3)")
    finally:
        con.close()
    return path


def _rows(lake: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (f"{DROPPED} LIMIT 5", 5),
        (f"{DROPPED} LIMIT 1", 1),
        (f"{DROPPED} UNION ALL {DROPPED} LIMIT 7", 7),
        (f"SELECT * FROM ({DROPPED} LIMIT 5) t", 5),
        (f"WITH t AS ({DROPPED} LIMIT 5) SELECT * FROM t", 5),
        (f"SELECT * FROM ({DROPPED}) t LIMIT 5", 5),
    ],
    ids=["top", "one", "union", "subquery", "cte", "outer"],
)
def test_limit_bounds_the_rows(lake: Path, sql: str, expected: int) -> None:
    assert len(_rows(lake, sql)) == expected


def test_order_by_limit_keeps_the_top_rows(lake: Path) -> None:
    rows = _rows(lake, f"{DROPPED} ORDER BY a.id DESC LIMIT 5")
    assert [r[0] for r in rows] == [99, 98, 97, 96, 95]


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT count(*) FROM ({DROPPED} LIMIT 5) t",
        f"WITH t AS ({DROPPED} LIMIT 5) SELECT count(*) FROM t",
        f"SELECT count(*) FROM a WHERE a.id IN ({DROPPED} LIMIT 5)",
    ],
    ids=["count-subquery", "count-cte", "in-subquery"],
)
def test_limit_inside_an_aggregate_is_kept(lake: Path, sql: str) -> None:
    assert _rows(lake, sql) == [(5,)]


def test_limit_with_offset_serves_the_page(lake: Path) -> None:
    rows = _rows(lake, f"{DROPPED} ORDER BY a.id LIMIT 5 OFFSET 10")
    assert [r[0] for r in rows] == [10, 11, 12, 13, 14]
