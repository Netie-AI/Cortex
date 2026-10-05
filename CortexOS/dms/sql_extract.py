"""Pull one query statement out of model text. The single extractor for FreeRoute SQL.

Engine generative-ask and Crew Insights generate both read model output through
this, so a fix to extraction lands once. Extraction is not validation: callers
still run the SQL gate / guardrail on what comes back.

Epic amendment on #277 (2026-10-01): a regex only finds the fenced block in
model prose (and the keyword offsets where a statement may start). Statement
boundaries, the one-statement rule and the SELECT / WITH-SELECT check come from
``sqlglot.parse(..., read="duckdb")``, not from a hand-written scanner:

* a fenced block must parse as exactly one query (``SELECT``, ``WITH ...
  SELECT``, a set operation) with a ``FROM``; anything else in the fence -- a
  second statement before or after it, DDL/DML behind a ``WITH`` -- refuses;
* unfenced text is tried from each ``SELECT`` / ``WITH`` keyword offset; the
  longest prefix (cut at ``;``, a newline or the end) that sqlglot parses as one
  query is the statement. Trailing or leading text that sqlglot parses as a
  statement of its own is a named refusal, never silently trimmed off; text
  sqlglot cannot parse (prose) is ignored.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import sqlglot
from sqlglot import exp

_SQL_FENCE = re.compile(r"```(?:sql|duckdb)?\s*(.*?)```", re.I | re.S)
_FENCE_MARK = re.compile(r"```(?:sql|duckdb)?", re.I)
#: Offsets where a statement may begin. Only a start finder: whether the text
#: from there is one query, and where it ends, is sqlglot's answer.
_START = re.compile(r"\b(?:SELECT|WITH)\b", re.I)
_CUT = re.compile(r"[;\n]")
_DIALECT = "duckdb"
_MULTI = "more than one statement"
#: Bounds on the work a hostile model output can cause (starts x cuts parses).
_MAX_TEXT = 50_000
_MAX_STARTS = 20
_MAX_CUTS = 60


class Extracted(NamedTuple):
    """``sql`` is the one statement, or None with ``reason`` naming why."""

    sql: str | None
    reason: str


def _statements(text: str) -> list[exp.Expression] | None:
    """sqlglot's statements for ``text`` (empty ones dropped), or None if it does not parse."""
    try:
        parsed = sqlglot.parse(text, read=_DIALECT)
    except Exception:  # noqa: BLE001 - any parse/token error means "not SQL here"
        return None
    # A bare ``;`` (or one carrying only a comment) parses as Semicolon: not a statement.
    return [node for node in parsed if node is not None and not isinstance(node, exp.Semicolon)]


def _query_reason(node: exp.Expression) -> str:
    """Empty when ``node`` is a read-only query with FROM; else why not."""
    if not isinstance(node, exp.Query) or isinstance(node, exp.Values):
        return f"not a SELECT query ({type(node).__name__.upper()} refused)"
    if node.find(exp.From) is None:
        return "extracted query has no FROM"
    return ""


def _kind(node: exp.Expression) -> str:
    """Statement keyword for ``node``; ``text`` when it is an expression, not a statement."""
    if isinstance(node, exp.Condition):
        return "text"  # a bare word or expression: sqlglot parses it, SQL does not run it
    if isinstance(node, exp.Command):
        return str(node.this or "COMMAND").upper()
    return type(node).__name__.upper()


def _prose_command(node: exp.Expression, raw: str) -> bool:
    """sqlglot's fallback Command for a sentence-case word ("Show this to ...")."""
    if not isinstance(node, exp.Command):
        return False
    word = raw.strip().split(None, 1)[0] if raw.strip() else ""
    return bool(word) and not (word.isupper() or word.islower())


def _sql_statement_in(text: str) -> str | None:
    """Keyword of the first SQL statement sqlglot finds in unfenced ``text``; None for prose."""
    for piece in text.split(";"):
        body = piece.strip()
        if not body:
            continue
        nodes = _statements(body)
        if not nodes:
            continue
        node = nodes[0]
        if isinstance(node, exp.Condition) or _prose_command(node, body):
            continue
        return _kind(node)
    return None


def _trim_tail(sql: str) -> str:
    """Drop a trailing ``;`` and anything after it that sqlglot reads as no statement."""
    text = sql.strip()
    for pos in reversed([m.start() for m in re.finditer(";", text)]):
        head, tail = text[:pos], text[pos + 1 :]
        if _statements(tail) == [] and len(_statements(head) or []) == 1:
            text = head.rstrip()
    return text


def _fenced(body: str) -> Extracted:
    nodes = _statements(body)
    if nodes is None:
        return Extracted(None, "fenced SQL does not parse")
    if not nodes:
        return Extracted(None, "no SELECT or WITH query in model output")
    if len(nodes) > 1:
        if isinstance(nodes[0], exp.Query):
            where, other = "trailing", nodes[1]
        else:
            where, other = "leading", nodes[0]
        return Extracted(None, f"{_MULTI} in model output ({where} {_kind(other)} refused)")
    reason = _query_reason(nodes[0])
    if reason:
        return Extracted(None, reason)
    return Extracted(_trim_tail(body), "")


def _unfenced(text: str) -> Extracted:
    reason = "no SELECT or WITH query in model output"
    for count, start in enumerate(m.start() for m in _START.finditer(text)):
        if count >= _MAX_STARTS:
            break
        cuts = [m.start() for m in _CUT.finditer(text, start)][:_MAX_CUTS]
        ends = sorted({*cuts, len(text)}, reverse=True)  # longest prefix first
        for end in ends:
            candidate = text[start:end]
            nodes = _statements(candidate)
            if not nodes or len(nodes) != 1:
                continue
            why = _query_reason(nodes[0])
            if why:
                reason = why
                if not why.startswith("extracted query has no FROM"):
                    # A WITH that parses as INSERT/DELETE/...: DDL/DML behind a
                    # WITH is a named refusal, not something to look past.
                    return Extracted(None, why)
                continue
            before = text[:start]
            if ";" in before:
                kind = _sql_statement_in(before[: before.rfind(";")].split(";")[-1])
                if kind:
                    return Extracted(None, f"{_MULTI} in model output (leading {kind} refused)")
            kind = _sql_statement_in(text[end:])
            if kind:
                return Extracted(None, f"{_MULTI} in model output (trailing {kind} refused)")
            return Extracted(_trim_tail(candidate), "")
    return Extracted(None, reason)


def extract_statement(text: str) -> Extracted:
    """One WITH/SELECT statement, or None with a named reason.

    Tried in order: each fenced block, the text with fenced blocks removed, the
    text with only the fence markers removed, the raw text. Models often fence
    only the SELECT list and leave ``FROM t i`` outside the fence; a candidate
    without FROM falls through to the next form. A second statement is final:
    output that carries one is refused, not rescued from another form.
    """
    if not text:
        return Extracted(None, "empty model output")
    if len(text) > _MAX_TEXT:
        return Extracted(None, f"model output longer than {_MAX_TEXT} characters")
    reason = ""
    for match in _SQL_FENCE.finditer(text):
        got = _fenced(match.group(1))
        if got.sql or not got.reason.startswith(
            ("extracted query has no FROM", "no SELECT or WITH")
        ):
            return got
        reason = reason or got.reason
    for body in (_SQL_FENCE.sub(" ", text), _FENCE_MARK.sub(" ", text), text):
        got = _unfenced(body)
        if got.sql or got.reason.startswith((_MULTI, "not a SELECT")):
            return got
        if not reason or reason.startswith("no SELECT"):
            reason = got.reason
    return Extracted(None, reason or "no SELECT or WITH query in model output")


def extract_select(text: str) -> str | None:
    """Take one WITH/SELECT statement that has FROM, or None (caller retries / abstains)."""
    return extract_statement(text).sql


__all__ = ["Extracted", "extract_select", "extract_statement"]
