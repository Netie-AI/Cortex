"""S1 — deterministic detectors over a lakehouse table. Pure SQL, no LLM.

A detector reduces a table to one scalar and compares it to a bound. This is the
cheap, always-on layer the research prescribes: the LLM agent only fires *after*
a deterministic detector trips, so we never pay model cost per stream window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packs.dms.lakehouse.catalog import LAKE_ALIAS, SCHEMAS, connect

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
_AGGS = {"count", "max", "min", "avg", "sum"}


class DetectorError(ValueError):
    pass


@dataclass(slots=True)
class Detection:
    fired: bool
    value: float | None
    bound: float
    op: str
    metric: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"fired": self.fired, "value": self.value, "bound": self.bound,
                "op": self.op, "metric": self.metric, "detail": self.detail}


def _ident(name: str, what: str) -> str:
    if not _IDENT.match(name or ""):
        raise DetectorError(f"unsafe {what}: {name!r}")
    return name


def _resolve_table(table: str) -> str:
    schema, _, name = table.partition(".")
    if schema not in SCHEMAS:
        raise DetectorError(f"table must be <schema>.<name> with schema in {SCHEMAS}")
    return f"{LAKE_ALIAS}.{_ident(schema, 'schema')}.{_ident(name, 'table')}"


def _build_value_sql(cfg: dict) -> tuple[str, str]:
    """Return (value_sql, metric_label) for a detector config.

    types:
      rowcount  : {table, where?}                 → COUNT(*)
      threshold : {table, agg, column, where?}    → agg(column)
      staleness : {table, ts_column, where?}      → seconds since latest ts
    """
    dtype = cfg.get("type")
    table = _resolve_table(cfg["table"])
    where = cfg.get("where")
    where_sql = ""
    if where:
        # read-only over the lake; still constrain to a single predicate shape
        if ";" in where or "--" in where:
            raise DetectorError("where clause may not contain ';' or '--'")
        where_sql = f" WHERE {where}"

    if dtype == "rowcount":
        return f"SELECT COUNT(*) FROM {table}{where_sql}", "rowcount"
    if dtype == "threshold":
        agg = str(cfg.get("agg", "max")).lower()
        if agg not in _AGGS:
            raise DetectorError(f"agg must be one of {_AGGS}")
        col = _ident(cfg["column"], "column")
        return f"SELECT {agg}(TRY_CAST({col} AS DOUBLE)) FROM {table}{where_sql}", f"{agg}({col})"
    if dtype == "staleness":
        col = _ident(cfg["ts_column"], "ts_column")
        return (f"SELECT date_diff('second', MAX(TRY_CAST({col} AS TIMESTAMP)), now()) "
                f"FROM {table}{where_sql}", f"staleness_s({col})")
    raise DetectorError(f"unknown detector type {dtype!r}")


def evaluate(cfg: dict, *, con=None) -> Detection:
    op = cfg.get("op", ">")
    if op not in _OPS:
        raise DetectorError(f"op must be one of {sorted(_OPS)}")
    bound = float(cfg.get("bound", 0))
    value_sql, metric = _build_value_sql(cfg)

    owns = con is None
    con = con or connect(read_only=True)
    try:
        row = con.execute(value_sql).fetchone()
        raw = row[0] if row else None
    except Exception as exc:  # noqa: BLE001 — a missing table just means "no data yet"
        return Detection(False, None, bound, op, metric, f"query failed: {exc!r}")
    finally:
        if owns:
            con.close()

    value = None if raw is None else float(raw)
    fired = value is not None and _OPS[op](value, bound)
    detail = f"{metric} = {value} {op} {bound} -> {'FIRED' if fired else 'ok'}"
    return Detection(fired, value, bound, op, metric, detail)
