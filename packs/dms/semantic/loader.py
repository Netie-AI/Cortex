"""Q1 — load + compile the governed semantic model (metrics, certified, values).

`compile_metric` is the only path that turns a metric id + slot values into SQL:
every parameter is validated against its declared kind, rendered with no free
string interpolation, and the finished SQL is re-parsed through the existing
sqlglot guardrail before it is allowed out. That is what lets the Q2 answer
engine treat a compiled metric as trustworthy without the LLM writing SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from packs.dms.semantic import values as valuedict

_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)", re.IGNORECASE)


def stated_tables(sql: str) -> tuple[str, ...]:
    """Tables a metric or certified template says it will read.

    Taken from the definition, not from compiled SQL at query time. Template
    slots (``{limit}``, ``{wh}``) sit outside FROM/JOIN identifiers.
    """
    seen: list[str] = []
    for match in _TABLE_REF.finditer(sql or ""):
        name = match.group(1).lower()
        if name not in seen:
            seen.append(name)
    return tuple(seen)

ROOT = Path(__file__).resolve().parents[3]
SEMANTIC_DIR = ROOT / "packs" / "dms" / "semantic"
METRICS_PATH = SEMANTIC_DIR / "metrics.yaml"
CERTIFIED_PATH = SEMANTIC_DIR / "certified_queries.yaml"


class SemanticError(ValueError):
    """Raised when a metric/param is invalid or compiled SQL fails the guardrail."""


@dataclass(slots=True)
class Metric:
    id: str
    kind: str
    sql: str
    synonyms: list[str] = field(default_factory=list)
    result_columns: list[str] = field(default_factory=list)
    params: dict[str, dict] = field(default_factory=dict)
    tables: tuple[str, ...] = ()


@dataclass(slots=True)
class CertifiedQuery:
    id: str
    question: str
    sql: str
    verified_by: str = ""
    verified_at: str = ""
    tags: list[str] = field(default_factory=list)
    tables: tuple[str, ...] = ()
    synonyms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticModel:
    metrics: dict[str, Metric]
    certified: list[CertifiedQuery]
    base: dict[str, Any]          # raw semantic_layer.yaml (tables/joins/glossary)

    def metric(self, metric_id: str) -> Metric:
        if metric_id not in self.metrics:
            raise SemanticError(f"unknown metric {metric_id!r}")
        return self.metrics[metric_id]


# ── param rendering (no free-string interpolation ever) ──────────────────────
def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _render_param(name: str, spec: dict, raw: Any, *, resolve: bool = True) -> str:
    kind = spec.get("kind")

    if kind == "int":
        try:
            v = int(raw)
        except (TypeError, ValueError) as exc:
            raise SemanticError(f"{name}: expected int, got {raw!r}") from exc
        lo, hi = spec.get("min", -(10**9)), spec.get("max", 10**9)
        if not (lo <= v <= hi):
            raise SemanticError(f"{name}={v} out of range [{lo},{hi}]")
        return str(v)

    if kind == "float":
        try:
            v = float(raw)
        except (TypeError, ValueError) as exc:
            raise SemanticError(f"{name}: expected number, got {raw!r}") from exc
        lo, hi = spec.get("min", -1e18), spec.get("max", 1e18)
        if not (lo <= v <= hi):
            raise SemanticError(f"{name}={v} out of range [{lo},{hi}]")
        return repr(v)

    if kind == "enum":
        allowed = spec.get("values") or []
        if raw not in allowed:
            raise SemanticError(f"{name}={raw!r} not in {allowed}")
        return str(raw)

    if kind == "value":
        column = spec["column"]
        if resolve:
            res = valuedict.resolve(str(raw), column)
            if not res.ok:
                raise SemanticError(
                    f"{name}: cannot resolve {raw!r} to a {column} value "
                    f"(did you mean {res.candidates[:3]}?)")
            return _q(res.value)
        # already a validated literal value
        if str(raw) not in valuedict.values_for(column):
            raise SemanticError(f"{name}={raw!r} is not a known {column} value")
        return _q(str(raw))

    if kind == "filter":
        # optional AND-clause fragment; empty when no value supplied
        if raw in (None, ""):
            return ""
        column, op = spec["column"], spec.get("op", "=")
        table = spec.get("table")
        col_ref = f"{table}.{column}" if table else column
        res = valuedict.resolve(str(raw), column) if resolve else None
        val = res.value if (res and res.ok) else raw
        if resolve and (res is None or not res.ok):
            raise SemanticError(f"{name}: cannot resolve {raw!r} to a {column} value")
        return f"AND {col_ref} {op} {_q(val)}"

    if kind == "not_in":
        # optional AND column NOT IN (...); empty when no exclusions
        if raw in (None, "", [], ()):
            return ""
        items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        column = spec["column"]
        resolve = bool(spec.get("resolve"))
        quoted: list[str] = []
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            if resolve:
                res = valuedict.resolve(text, column)
                if res is None or not res.ok:
                    raise SemanticError(
                        f"{name}: cannot resolve {text!r} to a {column} value"
                    )
                val = res.value
            else:
                val = text
            quoted.append(_q(val.upper() if column == "sku" else val))
        if not quoted:
            return ""
        return f"AND {column} NOT IN ({', '.join(quoted)})"

    if kind == "offset_clause":
        # Omit OFFSET 0 so golden/certified SQL stay byte-stable.
        try:
            v = int(raw or 0)
        except (TypeError, ValueError) as exc:
            raise SemanticError(f"{name}: expected int, got {raw!r}") from exc
        if v < 0:
            raise SemanticError(f"{name}={v} must be >= 0")
        return f" OFFSET {v}" if v else ""

    raise SemanticError(f"{name}: unknown param kind {kind!r}")


def _sample_value(spec: dict) -> Any:
    """A concrete value for compile-time validation of a param with no default."""
    kind = spec.get("kind")
    if "default" in spec:
        return spec["default"]
    if kind == "int":
        return spec.get("min", 1)
    if kind == "float":
        return spec.get("min", 0.0)
    if kind == "enum":
        return (spec.get("values") or [None])[0]
    if kind in ("value", "filter"):
        vals = valuedict.values_for(spec["column"])
        return vals[0] if vals else None
    if kind == "not_in":
        return []
    if kind == "offset_clause":
        return 0
    return None


def compile_metric(model: SemanticModel, metric_id: str,
                   params: dict[str, Any] | None = None, *, resolve: bool = True) -> str:
    """Validate params, render the template, and re-verify with the guardrail."""
    from CortexOS.dms.sql_guardrail import validate_sql

    metric = model.metric(metric_id)
    params = params or {}
    rendered: dict[str, str] = {}
    for pname, spec in metric.params.items():
        provided = pname in params
        raw = params.get(pname, spec.get("default"))
        if raw is None and not provided:
            if spec.get("kind") in ("filter", "not_in", "offset_clause") or spec.get("optional"):
                empty = [] if spec.get("kind") == "not_in" else (0 if spec.get("kind") == "offset_clause" else "")
                rendered[pname] = _render_param(pname, spec, empty, resolve=resolve)
                continue
            if spec.get("required") or spec.get("kind") in ("value",) and not spec.get("optional"):
                raise SemanticError(f"{metric_id}: missing required param {pname!r}")
        rendered[pname] = _render_param(pname, spec, raw, resolve=resolve)

    try:
        sql = metric.sql.format(**rendered)
    except KeyError as exc:
        raise SemanticError(f"{metric_id}: template slot {exc} has no param") from exc

    result = validate_sql(sql, model.base)
    if not result.passed or not result.safe_sql:
        raise SemanticError(
            f"{metric_id}: compiled SQL failed guardrail {result.violations}: {sql}")
    return result.safe_sql


# ── loading + validation ─────────────────────────────────────────────────────
def _load_metrics() -> dict[str, Metric]:
    data = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))
    metrics: dict[str, Metric] = {}
    for raw in data.get("metrics", []):
        sql = raw["sql"].strip()
        m = Metric(
            id=raw["id"], kind=raw["kind"], sql=sql,
            synonyms=list(raw.get("synonyms") or []),
            result_columns=list(raw.get("result_columns") or []),
            params=dict(raw.get("params") or {}),
            tables=stated_tables(sql),
        )
        if m.id in metrics:
            raise SemanticError(f"duplicate metric id {m.id!r}")
        metrics[m.id] = m
    return metrics


def _load_certified() -> list[CertifiedQuery]:
    data = yaml.safe_load(CERTIFIED_PATH.read_text(encoding="utf-8"))
    seen: set[str] = set()
    phrases: set[str] = set()
    out: list[CertifiedQuery] = []
    for raw in data.get("certified", []):
        if raw["id"] in seen:
            raise SemanticError(f"duplicate certified id {raw['id']!r}")
        seen.add(raw["id"])
        sql = raw["sql"].strip()
        synonyms = list(raw.get("synonyms") or [])
        for phrase in (raw["question"], *synonyms):
            key = re.sub(r"[^a-z0-9]+", " ", (phrase or "").lower()).strip()
            if key and key in phrases:
                raise SemanticError(f"duplicate certified phrase {phrase!r}")
            if key:
                phrases.add(key)
        out.append(CertifiedQuery(
            id=raw["id"], question=raw["question"], sql=sql,
            verified_by=raw.get("verified_by", ""), verified_at=raw.get("verified_at", ""),
            tags=list(raw.get("tags") or []),
            tables=stated_tables(sql),
            synonyms=synonyms,
        ))
    return out


@lru_cache(maxsize=1)
def load_all() -> SemanticModel:
    from CortexOS.dms.warehouse_db import load_semantic_layer

    model = SemanticModel(
        metrics=_load_metrics(),
        certified=_load_certified(),
        base=load_semantic_layer(),
    )
    return model


def reload() -> SemanticModel:
    load_all.cache_clear()
    valuedict.refresh()
    return load_all()


def normalize_certified_sql(cq: CertifiedQuery) -> tuple[str, list[str]]:
    """Value-norm certified SQL (VQ-01). Pack-side so the engine never names generative."""
    from packs.dms.generative.literal_normalize import normalize_sql_literals

    norm = normalize_sql_literals(cq.sql)
    if not norm.ok:
        return cq.sql, list(norm.violations)
    return (norm.sql or cq.sql), []


def validate_all(*, execute: bool = True) -> dict[str, Any]:
    """Compile every metric with sample params and execute every certified query.

    Returns a report; raises SemanticError on the first hard failure so the
    smoke test fails loudly. `execute=False` skips DB execution (parse-only).
    """
    model = load_all()
    report: dict[str, Any] = {"metrics": {}, "certified": {}, "errors": []}

    con = None
    if execute:
        from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

        # explain/validate is an offline tool: never take the exclusive write lock
        con = get_connection(DEFAULT_DB, read_only=True)
    try:
        for mid, metric in model.metrics.items():
            sample = {p: _sample_value(spec) for p, spec in metric.params.items()}
            sql = compile_metric(model, mid, sample)
            report["metrics"][mid] = sql
            if con is not None:
                con.execute(sql).fetchall()
        for cq in model.certified:
            if con is not None:
                con.execute(cq.sql).fetchall()
            report["certified"][cq.id] = "ok"
    finally:
        if con is not None:
            con.close()
    return report
