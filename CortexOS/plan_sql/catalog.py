"""Granted catalog for one SQL generation attempt.

The prompt sees the tables and columns the signed grant allows, intersected
with the ``schema_context`` string DMS sends (``AskRequest.schema_context``,
contract 1.7.0). That string is the SCHEMA-CONTEXT-01 shape, and
``CortexOS.insights.schema_context`` is its only parser and its only rule:
the ask route and ``POST /v1/insights`` refuse the same strings with the same
codes, and read the same tables, columns and joins out of the ones they take.

Nothing parsed from outside the grant is returned to the prompt builder.
Names are whatever the grant and the schema string contain. No pack list.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import replace

from cortex_contract.answer import AskPayload, AskPayloadColumn, AskPayloadJoin, AskPayloadTable

from CortexOS.identifier_fold import fold_identifier
from CortexOS.insights import schema_context as schema_ctx
from CortexOS.plan_sql.payload import PlanSqlRequest

#: The string parses and declares no table (DMS's over-budget fallback).
#: SCHEMA-CONTEXT-01's own code, a 200 abstain on both routes.
SCHEMA_CONTEXT_EMPTY = schema_ctx.SCHEMA_CONTEXT_EMPTY
#: The string declares tables and none of them is granted to this session.
SCHEMA_CONTEXT_UNGRANTED = "schema_context_ungranted"

_UNKNOWN_COL = re.compile(
    r"UNKNOWN_COLUMN:([A-Za-z_][A-Za-z0-9_]*)"
    r"|column\s+\"([A-Za-z_][A-Za-z0-9_]*)\""
    r"|column\s+'([A-Za-z_][A-Za-z0-9_]*)'",
    re.IGNORECASE,
)


def column_names(request: PlanSqlRequest) -> tuple[str, ...]:
    """Column names on the request the prompt will see, in listed order."""
    seen: list[str] = []
    for table in request.payload.tables:
        for column in table.columns:
            if column.name not in seen:
                seen.append(column.name)
    return tuple(seen)


def narrow(request: PlanSqlRequest, granted: Iterable[str]) -> tuple[PlanSqlRequest, str]:
    """Payload the prompt may see, and ``""``; or the request and a refusal code.

    No schema string (absent or blank): the payload is unchanged (the grant
    check already ran). With one, ``schema_context.parse_schema_context``
    reads it, and the prompt's tables are ``grant ∩ declared tables``, folded
    the DuckDB way. Columns, types and joins come only from that string. A
    join is kept only when both of its tables are. The payload's own tables,
    columns and joins are not offered.

    Fail closed for a direct caller: a value ``schema_context.problem``
    refuses returns its code (the route has already turned those into a
    422). A string that declares no table is ``SCHEMA_CONTEXT_EMPTY``, and
    one whose tables are all outside the grant is ``SCHEMA_CONTEXT_UNGRANTED``.
    """
    text = schema_ctx.supplied_schema(request.schema_context)
    if text is None:
        return request, ""
    found = schema_ctx.problem(request.schema_context)
    if found is not None:
        return request, found[0]
    catalog = schema_ctx.parse_schema_context(text)
    allowed = {fold_identifier(str(name)) for name in granted}
    tables: list[AskPayloadTable] = []
    for name, columns in catalog.tables.items():
        if name not in allowed:
            continue
        types = catalog.column_types.get(name, {})
        tables.append(
            AskPayloadTable(
                name=name,
                columns=[AskPayloadColumn(name=col, type=types.get(col)) for col in columns],
            )
        )
    if not tables:
        return request, SCHEMA_CONTEXT_UNGRANTED
    kept = {table.name for table in tables}
    joins = [
        AskPayloadJoin(left_table=lt, left_column=lc, right_table=rt, right_column=rc)
        for lt, lc, rt, rc in catalog.join_keys
        if lt in kept and rt in kept
    ]
    return replace(request, payload=AskPayload(tables=tables, joins=joins)), ""


def refusal_detail(code: str) -> str:
    """Why :func:`narrow` refused, for the abstain text. Names no table."""
    if code == SCHEMA_CONTEXT_EMPTY:
        return "schema_context declares no table; nothing to generate against"
    if code == SCHEMA_CONTEXT_UNGRANTED:
        return "schema_context declares no table this session is granted"
    return "schema_context is not usable"


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
