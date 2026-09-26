"""Pull one query statement out of model text. The single extractor for FreeRoute SQL.

Engine generative-ask and Crew Insights generate both read model output through
this, so a fix to extraction lands once. Extraction is not validation: callers
still run the SQL gate / guardrail on what comes back.

A statement starts at a top-level ``WITH <name> AS (`` head when one comes
before the first ``SELECT``, otherwise at the first ``SELECT``. Starting at the
first ``SELECT`` alone cut ``WITH a AS (SELECT ..) SELECT ..`` down to the CTE
body plus a stray ``)``, which every validator refused as a parse error.

It ends at the first ``;`` outside quotes and comments. What follows must be
nothing, comments, or (outside a fence) prose: another SQL statement after it
-- a second ``SELECT``, a ``DROP``, an ``INSERT`` -- is a named refusal, never
silently trimmed off. The statement must contain ``FROM`` outside string
literals, quoted identifiers and comments.
"""

from __future__ import annotations

import re
from typing import NamedTuple

_SQL_FENCE = re.compile(r"```(?:sql|duckdb)?\s*(.*?)```", re.I | re.S)
_FENCE_MARK = re.compile(r"```(?:sql|duckdb)?", re.I)
_SELECT = re.compile(r"\bSELECT\b", re.I)
# ``WITH [RECURSIVE] name [(cols)] AS [[NOT] MATERIALIZED] (`` then a query
# keyword: specific enough that the English word "with" in prose never matches.
_WITH_HEAD = re.compile(
    r"\bWITH\s+(?:RECURSIVE\s+)?"
    r"(?:\"[^\"]+\"|[A-Za-z_][\w$]*)\s*(?:\([^()]*\)\s*)?"
    r"AS\s*(?:NOT\s+)?(?:MATERIALIZED\s*)?\(\s*(?:SELECT|WITH|VALUES|FROM)\b",
    re.I,
)
_FROM = re.compile(r"\bfrom\b", re.I)
#: Words that open a SQL statement. Text after a top-level ``;`` that starts with
#: one of these (as SQL writes it: all upper or all lower case) is a second
#: statement; sentence-case ("Show", "With") outside a fence is prose.
_STATEMENT_START = re.compile(
    r"(?:SELECT|WITH|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|COPY|"
    r"PRAGMA|SET|RESET|INSTALL|LOAD|CALL|EXPORT|IMPORT|TRUNCATE|MERGE|REPLACE|"
    r"UPSERT|GRANT|REVOKE|VACUUM|CHECKPOINT|BEGIN|COMMIT|ROLLBACK|ABORT|USE|"
    r"EXPLAIN|DESCRIBE|SHOW|SUMMARIZE|VALUES|TABLE|PIVOT|UNPIVOT|FROM|EXECUTE|"
    r"PREPARE|DEALLOCATE)\b",
    re.I,
)
_MULTI = "more than one statement"


class Extracted(NamedTuple):
    """``sql`` is the one statement, or None with ``reason`` naming why."""

    sql: str | None
    reason: str


def _scan(text: str, start: int) -> tuple[int, str]:
    """(index of the first top-level ``;`` at/after ``start``, or len; code-only text).

    Code-only text is ``text[start:end]`` with string literals, quoted
    identifiers, ``$$`` strings and comments blanked, so keyword checks see
    only real SQL and a ``;`` inside a literal never ends the statement.
    """
    out: list[str] = []
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        two = text[i : i + 2]
        if ch == ";":
            return i, "".join(out)
        close = {"--": "\n", "/*": "*/", "$$": "$$"}.get(two)
        if close is not None:
            j = text.find(close, i + 2)
            j = n if j < 0 else j + (0 if close == "\n" else len(close))
        elif ch in ("'", '"'):
            j = i + 1
            while j < n:
                if text[j] == ch:
                    if text[j + 1 : j + 2] == ch:  # a doubled quote escapes itself
                        j += 2
                        continue
                    break
                j += 1
            j = min(j + 1, n)
        else:
            out.append(ch)
            i += 1
            continue
        out.append(" " * (j - i))
        i = j
    return n, "".join(out)


def _after_statement(text: str) -> str:
    """What trails a statement, minus whitespace, comments and bare ``;``."""
    rest = text
    while True:
        rest = rest.lstrip().lstrip(";").lstrip()
        if rest.startswith("--"):
            j = rest.find("\n")
            rest = "" if j < 0 else rest[j:]
        elif rest.startswith("/*"):
            j = rest.find("*/")
            rest = "" if j < 0 else rest[j + 2 :]
        else:
            return rest


def _second_statement(rest: str, *, fenced: bool) -> str | None:
    """The keyword of a second statement in ``rest``, or None when there is none."""
    if not rest:
        return None
    found = _STATEMENT_START.match(rest)
    if found:
        word = found.group(0)
        if fenced or word.isupper() or word.islower():
            return word.upper()
    return "text" if fenced else None


def _one_statement(body: str, *, fenced: bool) -> Extracted:
    select = _SELECT.search(body)
    head = _WITH_HEAD.search(body)
    if head and (select is None or head.start() < select.start()):
        start = head.start()
    elif select:
        start = select.start()
    else:
        return Extracted(None, "no SELECT or WITH query in model output")
    end, code = _scan(body, start)
    if end < len(body):
        second = _second_statement(_after_statement(body[end + 1 :]), fenced=fenced)
        if second:
            return Extracted(None, f"{_MULTI} in model output (trailing {second} refused)")
    if not _FROM.search(code):
        return Extracted(None, "extracted query has no FROM")
    return Extracted(body[start:end].strip(), "")


def extract_statement(text: str) -> Extracted:
    """One WITH/SELECT statement, or None with a named reason.

    Tried in order: each fenced block, the text with fenced blocks removed, the
    text with only the fence markers removed, the raw text. Models often fence
    only the SELECT list and leave ``FROM t i`` outside the fence; that parsed
    as ``SELECT i.sku LIMIT 1000`` and EXPLAIN died on alias ``i``, so a
    candidate without FROM falls through to the next form. A second statement
    is final: output that carries one is refused, not rescued from another form.
    """
    if not text:
        return Extracted(None, "empty model output")
    blobs = [(m.group(1).strip(), True) for m in _SQL_FENCE.finditer(text)]
    blobs.append((_SQL_FENCE.sub(" ", text).strip(), False))
    blobs.append((_FENCE_MARK.sub(" ", text).strip(), False))
    blobs.append((text.strip(), False))
    reason = ""
    for body, fenced in blobs:
        got = _one_statement(body, fenced=fenced)
        if got.sql or got.reason.startswith(_MULTI):
            return got
        if not reason or reason.startswith("no SELECT"):
            reason = got.reason
    return Extracted(None, reason)


def extract_select(text: str) -> str | None:
    """Take one WITH/SELECT statement that has FROM, or None (caller retries / abstains)."""
    return extract_statement(text).sql


__all__ = ["Extracted", "extract_select", "extract_statement"]
