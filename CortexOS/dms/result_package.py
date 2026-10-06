"""C-LOOP-C (#305): package an answered result as rows, SQL, chart spec and insight.

Pure: it takes the rows and the exact SQL that ran and returns the package. It
holds no database handle, calls no model and opens no network connection. The
insight is templated from the rows and every number in it is checked back
against them; the chart spec may only name row columns and never embeds values.
An abstain is never packaged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from cortex_contract.answer import Answer, Badge, ChartSpec, Insight, InsightFact

ENABLED_ENV = "CORTEX_RESULT_PACKAGE"
SERVED_BY = "cortex:result-package"
CHART_METHOD = "shape_rule.v1"
INSIGHT_METHOD = "template.v1"
# Same ceiling as the SQL guardrail's LIMIT, so a guarded answer is never cut.
MAX_PACKAGE_ROWS = 1000
VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"
ROWS_DATA: dict[str, str] = {"name": "rows"}

INSIGHT_NUMBER_NOT_IN_ROWS = "INSIGHT_NUMBER_NOT_IN_ROWS"
INSIGHT_FACT_MISMATCH = "INSIGHT_FACT_MISMATCH"
INSIGHT_WRITER_ERROR = "INSIGHT_WRITER_ERROR"
CHART_FIELD_NOT_IN_ROWS = "CHART_FIELD_NOT_IN_ROWS"
CHART_SPEC_NOT_ALLOWED = "CHART_SPEC_NOT_ALLOWED"
NO_CHARTABLE_SHAPE = "NO_CHARTABLE_SHAPE"
PACKAGE_WITHOUT_SQL = "PACKAGE_WITHOUT_SQL"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ABSTAIN_TOKENS = frozenset({"abstain", "blocked", "refused", "needs_clarification"})
_TOP_KEYS = frozenset({"$schema", "data", "mark", "encoding"})
_MARKS = frozenset({"bar", "line", "point", "text"})
_CHANNEL_KEYS = frozenset({"field", "type", "sort", "title", "aggregate", "timeUnit"})
_SORT_TOKENS = frozenset({"ascending", "descending", "x", "-x", "y", "-y"})
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T].*)?$")
_NUMBER = re.compile(r"(?:(?<![\w)])-)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

InsightWriter = Callable[[list[dict[str, Any]], list[str]], tuple[str, list[InsightFact]]]


class PackageRefused(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ResultPackage:
    rows: list[dict[str, Any]]
    sql: str
    chart_spec: ChartSpec
    insight: Insight


def result_package_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in _TRUTHY


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    return math.isfinite(float(value))


def _columns(rows: Sequence[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(str(key), None)
    return list(seen)


def _numeric_columns(rows: Sequence[dict[str, Any]], columns: list[str]) -> list[str]:
    out = []
    for col in columns:
        values = [row.get(col) for row in rows if row.get(col) is not None]
        if values and all(_is_number(v) for v in values):
            out.append(col)
    return out


def _is_temporal(rows: Sequence[dict[str, Any]], col: str) -> bool:
    values = [row.get(col) for row in rows if row.get(col) is not None]
    return bool(values) and all(
        isinstance(v, (date, datetime)) or (isinstance(v, str) and _ISO_DATE.match(v))
        for v in values
    )


def _fmt(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    x = float(value)
    if x.is_integer():
        return f"{int(x):,}"
    digits = 2 if abs(x) >= 1 else 4
    return f"{x:,.{digits}f}".rstrip("0").rstrip(".")


def _sha256_json(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- chart spec -------------------------------------------------------------


def build_chart_spec(
    rows: Sequence[dict[str, Any]], columns: list[str]
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick a Vega-Lite-style spec from the result shape, or say why there is none."""
    numeric = _numeric_columns(rows, columns)
    if not rows or not numeric:
        return None, NO_CHARTABLE_SHAPE
    dims = [c for c in columns if c not in numeric]
    measure = numeric[0]
    base: dict[str, Any] = {"$schema": VEGA_LITE_SCHEMA, "data": dict(ROWS_DATA)}
    if len(rows) == 1 and len(columns) == 1:
        return {**base, "mark": "text", "encoding": {
            "text": {"field": measure, "type": "quantitative"},
        }}, None
    if dims:
        dim = dims[0]
        if _is_temporal(rows, dim):
            return {**base, "mark": "line", "encoding": {
                "x": {"field": dim, "type": "temporal"},
                "y": {"field": measure, "type": "quantitative"},
            }}, None
        return {**base, "mark": "bar", "encoding": {
            "x": {"field": dim, "type": "nominal", "sort": "-y"},
            "y": {"field": measure, "type": "quantitative"},
        }}, None
    if len(numeric) >= 2:
        return {**base, "mark": "point", "encoding": {
            "x": {"field": numeric[0], "type": "quantitative"},
            "y": {"field": numeric[1], "type": "quantitative"},
        }}, None
    return None, NO_CHARTABLE_SHAPE


def _spec_fields(node: Any) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "field":
                out.append(value if isinstance(value, str) else repr(value))
            else:
                out.extend(_spec_fields(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_spec_fields(item))
    return out


def unknown_chart_fields(spec: dict[str, Any], columns: Sequence[str]) -> list[str]:
    known = set(columns)
    return [f for f in dict.fromkeys(_spec_fields(spec)) if f not in known]


def validate_chart_spec(spec: Any, columns: Sequence[str]) -> list[str]:
    """Return the row columns ``spec`` references, or refuse it."""
    if not isinstance(spec, dict):
        raise PackageRefused(CHART_SPEC_NOT_ALLOWED, "spec is not an object")
    fields = list(dict.fromkeys(_spec_fields(spec)))
    missing = unknown_chart_fields(spec, columns)
    if missing:
        raise PackageRefused(CHART_FIELD_NOT_IN_ROWS, f"fields not in rows: {missing}")
    extra = sorted(set(spec) - _TOP_KEYS)
    if extra:
        raise PackageRefused(CHART_SPEC_NOT_ALLOWED, f"keys not allowed: {extra}")
    if spec.get("data") != ROWS_DATA:
        raise PackageRefused(CHART_SPEC_NOT_ALLOWED, "data must bind the answer rows by name")
    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    if mark_type not in _MARKS:
        raise PackageRefused(CHART_SPEC_NOT_ALLOWED, f"mark not allowed: {mark_type!r}")
    encoding = spec.get("encoding")
    if not isinstance(encoding, dict) or not encoding:
        raise PackageRefused(CHART_SPEC_NOT_ALLOWED, "encoding is empty")
    for channel, definition in encoding.items():
        if not isinstance(definition, dict) or set(definition) - _CHANNEL_KEYS:
            raise PackageRefused(CHART_SPEC_NOT_ALLOWED, f"channel {channel!r} not allowed")
        if "field" not in definition and definition.get("aggregate") != "count":
            raise PackageRefused(CHART_SPEC_NOT_ALLOWED, f"channel {channel!r} names no column")
        sort = definition.get("sort")
        if sort is not None and not (isinstance(sort, str) and sort in _SORT_TOKENS):
            raise PackageRefused(CHART_SPEC_NOT_ALLOWED, f"channel {channel!r} sort not allowed")
    return fields


# --- insight ----------------------------------------------------------------


def template_insight(
    rows: list[dict[str, Any]], columns: list[str]
) -> tuple[str, list[InsightFact]]:
    """Deterministic insight. Every number it prints is a fact read off ``rows``."""
    n = len(rows)
    facts = [InsightFact(op="row_count", value=n)]
    noun = "row" if n == 1 else "rows"
    if n == 0:
        return "The query returned 0 rows.", facts
    numeric = _numeric_columns(rows, columns)
    if not numeric:
        return f"{n} {noun} returned with columns {', '.join(columns)}.", facts
    if n == 1:
        parts = []
        for col in columns[:6]:
            value = rows[0].get(col)
            if _is_number(value):
                facts.append(InsightFact(op="value", value=float(value), column=col, row_index=0))
                parts.append(f"{col} = {_fmt(value)}")
            elif value is not None:
                parts.append(f"{col} = {value}")
        return f"{n} {noun} returned: " + "; ".join(parts) + ".", facts
    measure = numeric[0]
    dims = [c for c in columns if c not in numeric]
    indexed = [(i, row[measure]) for i, row in enumerate(rows) if row.get(measure) is not None]
    hi_i, hi_v = max(indexed, key=lambda item: float(item[1]))
    lo_i, lo_v = min(indexed, key=lambda item: float(item[1]))
    facts.append(InsightFact(op="max", value=float(hi_v), column=measure, row_index=hi_i))
    facts.append(InsightFact(op="min", value=float(lo_v), column=measure, row_index=lo_i))

    def _at(i: int) -> str:
        if not dims or rows[i].get(dims[0]) is None:
            return ""
        return f" ({dims[0]} {rows[i][dims[0]]})"

    return (
        f"{n} {noun} returned. Highest {measure}: {_fmt(hi_v)}{_at(hi_i)}. "
        f"Lowest {measure}: {_fmt(lo_v)}{_at(lo_i)}."
    ), facts


def _labels(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> list[str]:
    labels = set(columns)
    for row in rows:
        for value in row.values():
            if value is not None and not _is_number(value):
                labels.add(str(value))
    return sorted((x for x in labels if x.strip()), key=len, reverse=True)


def _strip_labels(text: str, labels: list[str]) -> str:
    for label in labels:
        if label not in text:
            continue
        pattern = r"(?<![0-9A-Za-z_])(?<!\d[.,])" + re.escape(label) + r"(?![0-9A-Za-z_]|[.,]\d)"
        text = re.sub(pattern, " ", text)
    return text


def untraceable_numbers(text: str, rows: Sequence[dict[str, Any]]) -> list[str]:
    """Numbers in ``text`` that are neither a row cell nor the row count.

    Column names and non-numeric cell values are removed first, so a label such
    as ``WH-02`` is not read as a number. A number matches a value when it is
    that value rounded to the precision it is printed at.
    """
    columns = _columns(rows)
    known = [float(v) for row in rows for v in row.values() if _is_number(v)]
    known.append(float(len(rows)))
    bad = []
    for token in _NUMBER.findall(_strip_labels(text, _labels(rows, columns))):
        value = float(token.replace(",", ""))
        decimals = len(token.split(".", 1)[1]) if "." in token else 0
        tolerance = 0.5 * 10**-decimals + 1e-9 * max(1.0, abs(value))
        if not any(abs(value - k) <= tolerance for k in known):
            bad.append(token)
    return bad


def _same(a: Any, b: float) -> bool:
    return _is_number(a) and abs(float(a) - b) <= 1e-9 * max(1.0, abs(b))


def _check_fact(fact: InsightFact, rows: Sequence[dict[str, Any]]) -> bool:
    if fact.op == "row_count":
        return fact.column is None and fact.value == len(rows)
    if fact.op not in {"value", "max", "min"} or fact.column is None:
        return False
    i = fact.row_index
    if i is None or not 0 <= i < len(rows) or not _same(rows[i].get(fact.column), fact.value):
        return False
    if fact.op == "value":
        return True
    values = [float(r[fact.column]) for r in rows if _is_number(r.get(fact.column))]
    target = max(values) if fact.op == "max" else min(values)
    return _same(target, fact.value)


def validate_insight(
    text: Any, facts: Sequence[InsightFact], rows: Sequence[dict[str, Any]]
) -> None:
    """Refuse an insight unless every number it states traces to ``rows``."""
    if not isinstance(text, str) or not text.strip():
        raise PackageRefused(INSIGHT_FACT_MISMATCH, "insight text is empty")
    bad = untraceable_numbers(text, rows)
    if bad:
        raise PackageRefused(INSIGHT_NUMBER_NOT_IN_ROWS, f"numbers not in rows: {bad}")
    wrong = [f.model_dump() for f in facts if not _check_fact(f, rows)]
    if wrong:
        raise PackageRefused(INSIGHT_FACT_MISMATCH, f"facts do not recompute: {wrong}")


# --- package ----------------------------------------------------------------


def package_result(
    rows: Sequence[dict[str, Any]] | None,
    sql: str | None,
    *,
    insight_writer: InsightWriter | None = None,
    writer_method: str = "writer",
    served_at: str | None = None,
) -> ResultPackage:
    """Package an answered result: bounded rows, the SQL that ran, chart spec, insight.

    ``insight_writer`` replaces the template (for example a FreeRoute-backed
    writer owned elsewhere). Its text goes through the same check; a refused
    insight is served empty with ``served_reason`` naming the refusal.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise PackageRefused(PACKAGE_WITHOUT_SQL, "an answered result carries the SQL that ran")
    all_rows = list(rows or [])
    bounded = [dict(row) for row in all_rows[:MAX_PACKAGE_ROWS]]
    columns = _columns(bounded)
    stamp: dict[str, Any] = {
        "served_by": SERVED_BY,
        "served_at": served_at or _now(),
        "served_rows_sha256": _sha256_json(bounded),
        "served_sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "served_row_count": len(bounded),
        "served_rows_truncated": len(all_rows) > len(bounded),
    }

    spec, chart_reason = build_chart_spec(bounded, columns)
    fields: list[str] = []
    if spec is not None:
        try:
            fields = validate_chart_spec(spec, columns)
        except PackageRefused as exc:
            spec, chart_reason = None, exc.code
    chart = ChartSpec(
        spec=spec, fields=fields, served_method=CHART_METHOD, served_reason=chart_reason, **stamp
    )

    text: str | None
    facts: list[InsightFact]
    insight_reason: str | None = None
    try:
        if insight_writer is None:
            text, facts = template_insight(bounded, columns)
        else:
            text, facts = insight_writer(bounded, columns)
        validate_insight(text, facts, bounded)
    except PackageRefused as exc:
        text, facts, insight_reason = None, [], exc.code
    except Exception as exc:  # noqa: BLE001 - a failing writer never breaks the answer
        text, facts, insight_reason = None, [], f"{INSIGHT_WRITER_ERROR}:{type(exc).__name__}"
    insight = Insight(
        text=text,
        facts=facts,
        served_method=INSIGHT_METHOD if insight_writer is None else writer_method,
        served_reason=insight_reason,
        **stamp,
    )
    return ResultPackage(rows=bounded, sql=sql, chart_spec=chart, insight=insight)


def is_abstain(answer: Answer) -> bool:
    prov = answer.provenance
    return (
        prov.badge in (Badge.ABSTAIN, Badge.BLOCKED)
        or (prov.layer or "").lower() in _ABSTAIN_TOKENS
        or (answer.route or "").lower() in _ABSTAIN_TOKENS
    )


def package_answer(answer: Answer, *, insight_writer: InsightWriter | None = None) -> Answer:
    """Attach the package to an answered ``Answer``. An abstain is returned untouched."""
    if is_abstain(answer):
        return answer
    if answer.rows is None or not (answer.sql_used or "").strip():
        return answer
    package = package_result(answer.rows, answer.sql_used, insight_writer=insight_writer)
    update: dict[str, Any] = {"chart_spec": package.chart_spec, "insight": package.insight}
    if package.chart_spec.served_rows_truncated:
        update["rows"] = package.rows
    return answer.model_copy(update=update)


def apply_result_package(data: dict[str, Any]) -> Answer:
    """Contract-ask seam. Flag off: the 1.4.0 answer, byte for byte."""
    # The engine's flat dict carries a legacy, differently shaped ``chart_spec``;
    # these two Answer fields belong to this module only.
    data.pop("chart_spec", None)
    data.pop("insight", None)
    answer = Answer.model_validate(data)
    if not result_package_enabled():
        return answer
    return package_answer(answer)
