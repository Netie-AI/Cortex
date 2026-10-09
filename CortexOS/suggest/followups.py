"""SUGGEST (#308): follow-up questions grounded in an answered result.

Templated and deterministic: drill-downs on returned dimensions, comparisons,
trends and ontology join neighbours, built only from the grant, the schema and
the rows the answer returned. Every follow-up passes ``refuse`` before it is
served, and a follow-up a model rephrased passes the same check on its new
text, so a model can reword a question but never widen it.

Nothing here opens a database, calls a model or reads a pack. The ask seam
(``CortexOS.suggest.ask``) supplies the grant, schema and result; a model
``Rephrase`` is injected, and it only ever sees templates with every result
value masked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Protocol

SERVED_BY = "cortex:suggest-template"
MIN_FOLLOWUPS = 2
MAX_FOLLOWUPS = 5
MAX_QUESTION_CHARS = 200
KINDS = frozenset({"drill_down", "compare", "trend", "join_neighbour"})
TIME_TYPES = frozenset({"date", "datetime", "timestamp"})

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUOTED = re.compile(r"'([^']*)'")
_PLACEHOLDER = re.compile(r"\{v(\d+)\}")


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    types: Mapping[str, str]
    primary_key: str = ""


@dataclass(frozen=True)
class Join:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass(frozen=True)
class Schema:
    """Visible columns per table, plus every hidden column name (never suggestable)."""

    tables: Mapping[str, Table]
    joins: tuple[Join, ...] = ()
    hidden: frozenset[str] = frozenset()

    def names(self) -> frozenset[str]:
        out = set(self.tables) | set(self.hidden)
        for table in self.tables.values():
            out.update(table.columns)
        return frozenset(out)


@dataclass(frozen=True)
class Result:
    question: str
    rows: tuple[Mapping[str, Any], ...]
    tables: tuple[str, ...]
    granted: frozenset[str]

    @property
    def columns(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for row in self.rows:
            for key in row:
                seen.setdefault(str(key), None)
        return tuple(seen)

    def values(self) -> frozenset[str]:
        cells = (_cell(v) for row in self.rows for v in row.values())
        return frozenset(c for c in cells if c is not None)


@dataclass(frozen=True)
class Candidate:
    question: str
    kind: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    values: tuple[str, ...]
    template: str


@dataclass(frozen=True)
class Rephrased:
    """What a model returned for the masked templates, with its FreeRoute stamp."""

    texts: tuple[str | None, ...]
    served_by: str
    served_reason: str
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False


class Rephrase(Protocol):
    def __call__(self, question: str, templates: Sequence[str]) -> Rephrased | None: ...


@dataclass(frozen=True)
class Outcome:
    followups: list[dict[str, Any]]
    reason: str | None
    refused: tuple[tuple[str, str], ...] = ()


def _cell(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    return str(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_time(value: Any) -> bool:
    return isinstance(value, (date, datetime))


def allowed_tables(result: Result, schema: Schema) -> frozenset[str]:
    return frozenset(t for t in result.granted if t in schema.tables)


# -- the guard ------------------------------------------------------------------


def refuse(c: Candidate, result: Result, schema: Schema) -> str | None:
    """Why this follow-up may not be served, or None when it is fully grounded."""
    if c.kind not in KINDS:
        return f"unknown kind {c.kind!r}"
    text = c.question.strip()
    if not text or len(text) > MAX_QUESTION_CHARS:
        return "question is empty or too long"
    if not c.tables:
        return "names no table"
    allowed = allowed_tables(result, schema)
    for table in c.tables:
        if table not in allowed:
            return f"table {table} is outside the grant or schema"
    result_columns = set(result.columns)
    for column in c.columns:
        if "." in column:
            table, name = column.split(".", 1)
            if table not in c.tables or table not in allowed:
                return f"column {column} is outside the grant or schema"
            if name not in schema.tables[table].columns:
                return f"column {column} is not a visible schema column"
        elif column not in result_columns:
            return f"column {column} is not in the result"
    values = result.values()
    for value in c.values:
        if value not in values:
            return f"value {value!r} is not in the result"
    quoted = _QUOTED.findall(text)
    for value in quoted:
        if value not in c.values:
            return f"quotes {value!r} without grounding it"
    for value in c.values:
        if value not in quoted:
            return f"drops the value {value!r} it references"
    named = set(c.tables) | {col.rsplit(".", 1)[-1] for col in c.columns}
    known = schema.names() | result_columns
    for token in _IDENT.findall(_QUOTED.sub(" ", text)):
        if token in named:
            continue
        if "_" in token or token in known or token.lower() in known:
            return f"names {token} without grounding it"
    for table in c.tables:
        if table not in _IDENT.findall(text):
            return f"drops the table {table} it references"
    return None


# -- templates ------------------------------------------------------------------


def _owner(column: str, read: Sequence[str], schema: Schema) -> str | None:
    for table in read:
        if column in schema.tables[table].columns:
            return table
    return None


def propose(result: Result, schema: Schema) -> list[Candidate]:
    """Deterministic candidates. Not trusted: ``suggest`` refuses each one."""
    allowed = allowed_tables(result, schema)
    read = [t for t in dict.fromkeys(result.tables) if t in allowed]
    if not read or not result.rows:
        return []
    first = result.rows[0]
    columns = result.columns
    measures = [c for c in columns if _is_number(first.get(c))]
    dims = [
        (owner, c)
        for c in columns
        if isinstance(first.get(c), str) and (owner := _owner(c, read, schema))
    ]
    has_time = any(_is_time(first.get(c)) for c in columns) or any(
        schema.tables[t].types.get(c) in TIME_TYPES for t, c in dims
    )
    out: list[Candidate] = []

    for table, dim in dims[:1]:
        value = str(first[dim])
        out.append(Candidate(
            f"Show the {table} rows where {dim} is '{value}'.",
            "drill_down", (table,), (f"{table}.{dim}",), (value,),
            "drill_down:returned_dimension",
        ))
        distinct = list(dict.fromkeys(str(r[dim]) for r in result.rows if isinstance(r.get(dim), str)))
        if measures and len(distinct) >= 2:
            a, b = distinct[:2]
            out.append(Candidate(
                f"Compare {measures[0]} for {dim} '{a}' versus '{b}' in {table}.",
                "compare", (table,), (measures[0], f"{table}.{dim}"), (a, b),
                "compare:returned_dimension",
            ))

    if measures and not has_time:
        for table in read:
            spec = schema.tables[table]
            when = next((c for c in spec.columns if spec.types.get(c) in TIME_TYPES), None)
            if when:
                out.append(Candidate(
                    f"How has {measures[0]} changed over {when} in {table}?",
                    "trend", (table,), (measures[0], f"{table}.{when}"), (),
                    "trend:time_column",
                ))
                break

    if measures:
        breakdowns = 0
        for table in read:
            spec = schema.tables[table]
            for col in spec.columns:
                if breakdowns >= 2:
                    break
                if spec.types.get(col) != "string" or col == spec.primary_key or col in columns:
                    continue
                out.append(Candidate(
                    f"Break down {measures[0]} by {col} in {table}.",
                    "drill_down", (table,), (measures[0], f"{table}.{col}"), (),
                    "drill_down:new_dimension",
                ))
                breakdowns += 1

    neighbours = 0
    for table in read:
        for j in schema.joins:
            if neighbours >= 2:
                break
            if j.from_table == table:
                near, other, other_col = j.from_column, j.to_table, j.to_column
            elif j.to_table == table:
                near, other, other_col = j.to_column, j.from_table, j.from_column
            else:
                continue
            if other not in allowed or other in read:
                continue
            out.append(Candidate(
                f"Which {other} rows link to these {table} results through {near}?",
                "join_neighbour", (table, other), (f"{table}.{near}", f"{other}.{other_col}"), (),
                "join_neighbour:ontology_link",
            ))
            neighbours += 1

    # Round-robin by kind, so the cap keeps one of each kind before a second of any.
    per_kind: dict[str, int] = {}
    ranked: list[tuple[int, int, Candidate]] = []
    for i, c in enumerate({c.question: c for c in out}.values()):
        per_kind[c.kind] = per_kind.get(c.kind, 0) + 1
        ranked.append((per_kind[c.kind], i, c))
    return [c for _, _, c in sorted(ranked, key=lambda r: (r[0], r[1]))]


# -- serve ----------------------------------------------------------------------


def _mask(c: Candidate) -> str:
    text = c.question
    for i, value in enumerate(c.values, start=1):
        text = text.replace(f"'{value}'", f"{{v{i}}}")
    return text


def _unmask(text: str, c: Candidate) -> str:
    def sub(m: re.Match[str]) -> str:
        i = int(m.group(1)) - 1
        return f"'{c.values[i]}'" if 0 <= i < len(c.values) else m.group(0)

    return _PLACEHOLDER.sub(sub, text)


def _served(
    c: Candidate,
    now: str,
    *,
    by: str = SERVED_BY,
    reason: str = "",
    provider: str | None = None,
    model: str | None = None,
    local: bool = False,
) -> dict[str, Any]:
    return {
        "question": c.question,
        "kind": c.kind,
        "tables": list(c.tables),
        "columns": list(c.columns),
        "values": list(c.values),
        "served_by": by,
        "served_at": now,
        "served_reason": reason or f"template:{c.template}",
        "served_provider": provider,
        "served_model": model,
        "served_local": local,
    }


def suggest(
    result: Result,
    schema: Schema,
    *,
    rephrase: Rephrase | None = None,
    now: str | None = None,
) -> Outcome:
    stamp = now or datetime.now(timezone.utc).isoformat()
    kept: list[Candidate] = []
    refused: list[tuple[str, str]] = []
    for c in propose(result, schema):
        why = refuse(c, result, schema)
        if why is None:
            kept.append(c)
        else:
            refused.append((c.question, why))
    kept = kept[:MAX_FOLLOWUPS]
    if len(kept) < MIN_FOLLOWUPS:
        return Outcome([], f"fewer than {MIN_FOLLOWUPS} grounded follow-ups", tuple(refused))

    model: Rephrased | None = None
    if rephrase is not None:
        try:
            model = rephrase(result.question, [_mask(c) for c in kept])
        except Exception:  # noqa: BLE001 - a model failure falls back to templates
            model = None

    served: list[dict[str, Any]] = []
    for i, c in enumerate(kept):
        text = model.texts[i] if model is not None and i < len(model.texts) else None
        if model is not None and isinstance(text, str) and text.strip():
            worded = replace(c, question=_unmask(text.strip(), c))
            why = refuse(worded, result, schema)
            if why is None:
                served.append(_served(
                    worded, stamp,
                    by=model.served_by,
                    reason=f"template:{c.template}; rephrased ({model.served_reason})",
                    provider=model.served_provider,
                    model=model.served_model,
                    local=model.served_local,
                ))
                continue
            refused.append((worded.question, why))
        served.append(_served(c, stamp))
    return Outcome(served, None, tuple(refused))
