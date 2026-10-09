"""Granted catalog for one SQL generation attempt.

The prompt sees the tables and columns the signed grant allows, intersected
with the ``schema_context`` string DMS sends. That string is the SCHEMA-CONTEXT-01
shape (PR #351, contract 1.5.0): optional text, ``SCHEMA`` table/column lines,
``JOINS`` lines. This module does not add a contract field.

Nothing parsed from outside the grant is returned to the prompt builder.
Names are whatever the grant and the schema string contain. No pack list.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from cortex_contract.answer import AskPayload, AskPayloadColumn, AskPayloadJoin, AskPayloadTable

from CortexOS.plan_sql.payload import PlanSqlRequest

# Same cap as SCHEMA-CONTEXT-01. Over-cap is a named refusal, never a truncation.
SCHEMA_CONTEXT_MAX_CHARS = 8192
SCHEMA_CONTEXT_INVALID = "schema_context_invalid"
SCHEMA_CONTEXT_TOO_LARGE = "schema_context_too_large"
SCHEMA_CONTEXT_EMPTY = "PLAN_SQL_SCHEMA_CONTEXT_EMPTY"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+$")
_JOIN = re.compile(
    r"(?P<lt>[A-Za-z_][A-Za-z0-9_]*)\.(?P<lc>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*=\s*"
    r"(?P<rt>[A-Za-z_][A-Za-z0-9_]*)\.(?P<rc>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*\((?P<rel>[^)]*)\))?"
)
_UNKNOWN_COL = re.compile(
    r"UNKNOWN_COLUMN:([A-Za-z_][A-Za-z0-9_]*)"
    r"|column\s+\"([A-Za-z_][A-Za-z0-9_]*)\""
    r"|column\s+'([A-Za-z_][A-Za-z0-9_]*)'",
    re.IGNORECASE,
)


def _norm(name: str) -> str:
    return name.strip().lower()


def schema_context_problem(value: object) -> tuple[str, str] | None:
    """Named refusal for a present-but-unusable ``schema_context``, else None.

    ``None`` and a blank string are absent, not an error. A non-string is
    invalid. Over-cap is never truncated.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return (SCHEMA_CONTEXT_INVALID, "schema_context must be a string")
    if len(value) > SCHEMA_CONTEXT_MAX_CHARS:
        return (
            SCHEMA_CONTEXT_TOO_LARGE,
            f"schema_context length {len(value)} exceeds {SCHEMA_CONTEXT_MAX_CHARS}",
        )
    return None


def accepted_schema_context(value: object) -> str | None:
    """Stripped schema string, or None when the field is absent or unusable."""
    if schema_context_problem(value) is not None or not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


@dataclass(frozen=True)
class _Column:
    name: str
    type: str


@dataclass(frozen=True)
class _Table:
    name: str
    columns: tuple[_Column, ...]


@dataclass(frozen=True)
class _Parsed:
    tables: tuple[_Table, ...]
    joins: tuple[AskPayloadJoin, ...]


def _words(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("-"):
        body = body[1:].strip()
    words: list[str] = []
    for tok in body.split():
        if _KV.match(tok) or tok.startswith("reason="):
            continue
        words.append(tok)
    return words


def parse_schema_context(text: str) -> _Parsed:
    """Tables, columns and joins named by a schema_context string.

    A ``SCHEMA`` line with one identifier is a table. A line with an identifier
    plus a type token is a column of the current table. ``JOINS`` lines are
    ``table.column = table.column``. Anything else is dropped, so unparsed
    prose never reaches the prompt.
    """
    section = ""
    current: str | None = None
    buckets: dict[str, list[_Column]] = {}
    order: list[str] = []
    joins: list[AskPayloadJoin] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "SCHEMA" or upper.startswith("SCHEMA "):
            section = "schema"
            continue
        if upper == "JOINS" or upper.startswith("JOINS "):
            section = "joins"
            continue
        if upper.startswith("DIALECT:"):
            continue
        if section == "joins":
            match = _JOIN.search(line)
            if match is None:
                continue
            rel = (match.group("rel") or "").strip() or None
            joins.append(
                AskPayloadJoin(
                    left_table=match.group("lt"),
                    left_column=match.group("lc"),
                    right_table=match.group("rt"),
                    right_column=match.group("rc"),
                    relation=rel,
                )
            )
            continue
        if section != "schema":
            continue
        words = _words(line)
        if not words or not _IDENT.match(words[0]):
            continue
        if len(words) == 1:
            current = words[0]
            if current not in buckets:
                buckets[current] = []
                order.append(current)
            continue
        if current is None or not _IDENT.match(words[1]):
            continue
        buckets[current].append(_Column(words[0], words[1]))
    tables = tuple(_Table(name, tuple(buckets[name])) for name in order)
    return _Parsed(tables, tuple(joins))


def column_names(request: PlanSqlRequest) -> tuple[str, ...]:
    """Column names on the request the prompt will see, in listed order."""
    seen: list[str] = []
    for table in request.payload.tables:
        for column in table.columns:
            if column.name not in seen:
                seen.append(column.name)
    return tuple(seen)


def narrow(request: PlanSqlRequest, granted: Iterable[str]) -> tuple[PlanSqlRequest, str]:
    """Payload the prompt may see.

    No schema string: the payload is unchanged (the grant check already ran).
    With a schema string: tables are ``grant ∩ schema_context``. Columns and
    joins come only from that string. ``SCHEMA_CONTEXT_EMPTY`` when the
    intersection names no table.
    """
    text = accepted_schema_context(request.schema_context)
    if text is None:
        return request, ""
    allowed = {_norm(name) for name in granted}
    parsed = parse_schema_context(text)
    tables: list[AskPayloadTable] = []
    kept: set[str] = set()
    for table in parsed.tables:
        if _norm(table.name) not in allowed:
            continue
        kept.add(_norm(table.name))
        tables.append(
            AskPayloadTable(
                name=table.name,
                columns=[AskPayloadColumn(name=col.name, type=col.type or None) for col in table.columns],
            )
        )
    if not tables:
        return request, SCHEMA_CONTEXT_EMPTY
    joins = [
        join
        for join in parsed.joins
        if _norm(join.left_table) in kept and _norm(join.right_table) in kept
    ]
    return replace(request, payload=AskPayload(tables=tables, joins=joins)), ""


def unknown_column_names(detail: str) -> tuple[str, ...]:
    """Column identifiers an error named. Order kept, duplicates dropped."""
    found: list[str] = []
    for match in _UNKNOWN_COL.finditer(detail or ""):
        name = next((group for group in match.groups() if group), "")
        if name and name not in found:
            found.append(name)
    return tuple(found)


def did_you_mean(unknown: Sequence[str], columns: Sequence[str]) -> str:
    """Hint line from the granted catalog, or ``""`` when there is nothing to suggest.

    Close names come first. When nothing is close, the hint still names columns
    from the catalog so the next attempt is not shown the rest of the ontology.
    """
    pool = list(dict.fromkeys(col for col in columns if col))
    if not unknown or not pool:
        return ""
    suggestions: list[str] = []
    blocked = {name.lower() for name in unknown}
    for name in unknown:
        close = difflib.get_close_matches(name, pool, n=3, cutoff=0.4)
        for item in close:
            if item.lower() not in blocked and item not in suggestions:
                suggestions.append(item)
    if not suggestions:
        for item in pool:
            if item.lower() not in blocked and item not in suggestions:
                suggestions.append(item)
            if len(suggestions) >= 3:
                break
    if not suggestions:
        return ""
    return "did you mean: " + ", ".join(suggestions[:3])


def with_did_you_mean(detail: str, columns: Sequence[str]) -> str:
    """Append a catalog hint when ``detail`` names an unknown column."""
    hint = did_you_mean(unknown_column_names(detail), columns)
    if not hint or hint in detail:
        return detail
    return f"{detail}; {hint}"
