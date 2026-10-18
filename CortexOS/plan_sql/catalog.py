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
from CortexOS.plan_sql.payload import PlanSqlRequest, schema_block

#: The string parses and declares no table (DMS's over-budget fallback).
#: SCHEMA-CONTEXT-01's own code, a 200 abstain on both routes.
SCHEMA_CONTEXT_EMPTY = schema_ctx.SCHEMA_CONTEXT_EMPTY
#: The string declares tables and none of them is granted to this session.
SCHEMA_CONTEXT_UNGRANTED = "schema_context_ungranted"

#: Where an error names a column that does not exist: the SQL gate's
#: ``UNKNOWN_COLUMN:<name>``, DuckDB's ``Referenced column "<name>" not found``
#: and its qualified form ``Table "t" does not have a column named "<name>"``.
_UNKNOWN_COL = re.compile(
    r"UNKNOWN_COLUMN:([A-Za-z_][A-Za-z0-9_]*)"
    r"|column\s+(?:named\s+)?\"([A-Za-z_][A-Za-z0-9_]*)\""
    r"|column\s+(?:named\s+)?'([A-Za-z_][A-Za-z0-9_]*)'",
    re.IGNORECASE,
)

#: A did-you-mean name is a typo of the unknown one: at most this many edits
#: (insert, delete, replace, or swap two adjacent characters), case ignored.
_TYPO_EDITS = 1
#: And at least this similar (``difflib`` ratio), so one changed letter in a
#: short name is not a typo of a different word (``date`` is not ``data``).
_TYPO_RATIO = 0.8
#: An edit inside a ``_``-separated token shorter than this (both spellings)
#: changes a unit, a currency or a period, not a letter of a word:
#: ``quantity_g`` is not a typo of ``quantity_kg``.
_SHORT_TOKEN = 4
_MAX_HINTS = 3
_SQL_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: The database's own suggestions in an error it raised: DuckDB's binder
#: ``Candidate bindings: "a", "t.b"``, its qualified form on a base table
#: ``Candidate bindings: : "a"``, and its catalog ``Did you mean "x"?``.
#: They name lake columns whether or not they are close to the unknown name
#: and whether or not the prompt's granted catalog holds them.
_DB_SUGGESTION = re.compile(
    r"\s*(?:Candidate bindings:(?:\s*:?\s*\"[^\"]*\"\s*,?)*(?:\s*:)?"
    r"|Did you mean\s*\"[^\"]*\"\s*\??)",
    re.IGNORECASE,
)


def column_names(request: PlanSqlRequest, granted: Iterable[str]) -> tuple[str, ...]:
    """Column names of granted tables on the request the prompt sees, in listed order.

    The pool :func:`did_you_mean` draws from. A table outside ``granted``
    (folded the DuckDB way) contributes nothing, so a hint can only name a
    column of a granted table that the prompt already showed.
    """
    allowed = {fold_identifier(str(name).strip()) for name in granted}
    seen: list[str] = []
    for table in request.payload.tables:
        if fold_identifier(table.name.strip()) not in allowed:
            continue
        for column in table.columns:
            if column.name not in seen:
                seen.append(column.name)
    return tuple(seen)


def unmasked_columns(payload: AskPayload) -> frozenset[tuple[str, str]]:
    """``(table, column)`` pairs, folded, that every listing marks ``masked: false``.

    Fail closed: a column listed twice is unmasked only if both listings say
    so, and a column with ``masked`` true or absent is not in the set.
    """
    states: dict[tuple[str, str], set[bool | None]] = {}
    for table in payload.tables:
        for column in table.columns:
            key = (fold_identifier(table.name.strip()), fold_identifier(column.name))
            states.setdefault(key, set()).add(column.masked)
    return frozenset(key for key, seen in states.items() if seen == {False})


def sample_budget(request: PlanSqlRequest) -> int:
    """UTF-8 bytes the sampled-values block may take, inside #351's cap.

    ``SCHEMA_CONTEXT_MAX_BYTES`` (8192) bounds the schema and its samples
    together. The schema's share is the larger of the ``schema_context``
    string DMS sent and the schema block the prompt shows, so a sampled value
    never takes the prompt past what that cap allowed the schema alone. A
    schema at the cap leaves nothing, and nothing is sampled into the prompt.
    """
    used = len(schema_block(request).encode("utf-8"))
    if schema_ctx.supplied_schema(request.schema_context) is not None:
        used = max(used, len(str(request.schema_context).encode("utf-8")))
    return max(0, schema_ctx.SCHEMA_CONTEXT_MAX_BYTES - used)


def narrow(request: PlanSqlRequest, granted: Iterable[str]) -> tuple[PlanSqlRequest, str]:
    """Payload the prompt may see, and ``""``; or the request and a refusal code.

    No schema string (absent or blank): the payload is unchanged (the grant
    check already ran). With one, ``schema_context.parse_schema_context``
    reads it, and the prompt's tables are ``grant ∩ declared tables``, folded
    the DuckDB way. Columns, types and joins come only from that string. A
    join is kept only when both of its tables are. The payload's own tables,
    columns and joins are not offered. The one thing read from the payload is
    a kept column's ``masked`` state (contract 1.7.0), matched by folded table
    and column name; a column the payload does not list has none, so the
    sampler leaves it alone.

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
    unmasked = unmasked_columns(request.payload)
    tables: list[AskPayloadTable] = []
    for name, columns in catalog.tables.items():
        if name not in allowed:
            continue
        types = catalog.column_types.get(name, {})
        tables.append(
            AskPayloadTable(
                name=name,
                columns=[
                    AskPayloadColumn(
                        name=col,
                        type=types.get(col),
                        masked=False if (name, col) in unmasked else None,
                    )
                    for col in columns
                ],
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


def _edits(left: str, right: str) -> int:
    """Optimal-string-alignment distance (an adjacent swap is one edit)."""
    rows = [list(range(len(right) + 1))]
    for i, a in enumerate(left, 1):
        row = [i] + [0] * len(right)
        for j, b in enumerate(right, 1):
            row[j] = min(
                rows[-1][j] + 1,
                row[j - 1] + 1,
                rows[-1][j - 1] + (a != b),
            )
            if i > 1 and j > 1 and a == right[j - 2] and left[i - 2] == b:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _token(name: str, start: int, end: int) -> str:
    """The ``_``-separated token of ``name`` around ``name[start:end]``."""
    left = name.rfind("_", 0, start) + 1
    right = name.find("_", end)
    return name[left : len(name) if right < 0 else right]


def _edit_is_a_different_word(left: str, right: str) -> bool:
    """True when the one edit between ``left`` and ``right`` changes a quantity.

    The edit is where the two names stop sharing a prefix and a suffix. It is
    a different word, not a typo, when it changes a digit (``fy2024`` and
    ``fy2025``, ``q1_sales`` and ``q2_sales``), or when it falls inside a
    ``_``-separated token shorter than ``_SHORT_TOKEN`` in both names
    (``quantity_g`` and ``quantity_kg``, ``amount_usd`` and ``amount_usb``).
    A structural rule: no unit, currency or period is listed.
    """
    prefix = 0
    while prefix < min(len(left), len(right)) and left[prefix] == right[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(left), len(right)) - prefix
        and left[-1 - suffix] == right[-1 - suffix]
    ):
        suffix += 1
    changed_left = left[prefix : len(left) - suffix]
    changed_right = right[prefix : len(right) - suffix]
    changed = changed_left + changed_right
    if any(ch.isdigit() for ch in changed):
        return True
    if "_" in changed:
        return False
    tokens = (
        _token(left, prefix, len(left) - suffix),
        _token(right, prefix, len(right) - suffix),
    )
    return max(len(token) for token in tokens) < _SHORT_TOKEN


def _is_typo(unknown: str, column: str) -> bool:
    left, right = unknown.lower(), column.lower()
    if left == right or abs(len(left) - len(right)) > _TYPO_EDITS:
        return False
    if _edits(left, right) > _TYPO_EDITS:
        return False
    if _edit_is_a_different_word(left, right):
        return False
    return difflib.SequenceMatcher(None, left, right).ratio() >= _TYPO_RATIO


def did_you_mean(unknown: Sequence[str], columns: Sequence[str]) -> str:
    """``did you mean: <names>`` naming typos of an unknown column, else ``""``.

    ``columns`` is the granted catalog the prompt showed (:func:`column_names`).
    A column is offered only when it is a typo of the unknown name: one edit,
    case ignored, and a ``difflib`` ratio of at least 0.8 (``txn_typo`` gives
    ``txn_type``). An edit that changes a digit, or falls inside a short
    ``_``-separated token, names a different unit or period and is not a typo
    (``quantity_g`` gets no ``quantity_kg``, ``fy2024_sales`` no
    ``fy2025_sales``). Nothing else is offered and there is no fallback. A column
    the question needs that is not in the catalog (``revenue`` on a table of
    ``quantity_kg``) gets no hint: offering a column that is not close is an
    invitation to answer a different question under the asked-for name.
    """
    pool = list(dict.fromkeys(col for col in columns if col))
    if not unknown or not pool:
        return ""
    blocked = {name.lower() for name in unknown}
    suggestions: list[str] = []
    for name in unknown:
        for item in pool:
            if item.lower() in blocked or item in suggestions:
                continue
            if _is_typo(name, item):
                suggestions.append(item)
    if not suggestions:
        return ""
    return "did you mean: " + ", ".join(suggestions[:_MAX_HINTS])


def drop_db_suggestions(detail: str) -> str:
    """``detail`` without the database's own did-you-mean lists."""
    return _DB_SUGGESTION.sub("", detail or "")


def retry_detail(detail: str, columns: Sequence[str]) -> str:
    """A failure line for the error-fed retry prompt.

    The database's own suggestions are removed, and a hint is added only when
    a column of the granted catalog (``columns``) is a typo of an unknown one.
    So the only names the retry is offered are :func:`did_you_mean`'s.
    """
    return with_did_you_mean(drop_db_suggestions(detail), columns)


def with_did_you_mean(detail: str, columns: Sequence[str]) -> str:
    """``detail`` plus a catalog hint when it names a typo of a catalog column."""
    hint = did_you_mean(unknown_column_names(detail), columns)
    if not hint or hint in detail:
        return detail
    return f"{detail}; {hint}"


def unknown_column_hint(error_text: str, sql: str, columns: Sequence[str]) -> str:
    """``UNKNOWN_COLUMN:<names>; did you mean: ...`` for a database error, or ``""``.

    For an executor error, whose text is not fed back on its own. Only the
    unknown names the model's ``sql`` itself wrote are named (never a column a
    manifest predicate added), and only when :func:`did_you_mean` has a typo
    match for one of them. The database's own wording, including any candidate
    columns it lists, is not part of the result.
    """
    words = {word.lower() for word in _SQL_WORD.findall(sql or "")}
    names = [name for name in unknown_column_names(error_text) if name.lower() in words]
    hint = did_you_mean(names, columns)
    if not hint:
        return ""
    return f"UNKNOWN_COLUMN:{','.join(names)}; {hint}"
