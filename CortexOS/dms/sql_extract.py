"""Pull one SELECT out of model text. The single extractor for FreeRoute SQL.

Engine generative-ask and Crew Insights generate both read model output through
this, so a fix to extraction lands once. Extraction is not validation: callers
still run the SQL gate / guardrail on what comes back.
"""

from __future__ import annotations

import re

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.I | re.S)
_SELECT = re.compile(r"(SELECT\b.+)", re.I | re.S)
_FROM = re.compile(r"\bfrom\b", re.I)


def _one_select(body: str) -> str | None:
    found = _SELECT.search(body)
    if not found:
        return None
    sql = found.group(1).strip().rstrip(";")
    if ";" in sql:
        sql = sql.split(";", 1)[0].strip()
    if not sql.upper().startswith("SELECT"):
        return None
    return sql


def extract_select(text: str) -> str | None:
    """Take one SELECT. Prefer a statement that still has FROM.

    Models often fence only the SELECT list and leave ``FROM t i`` outside the
    fence. That parsed as ``SELECT i.sku LIMIT 1000`` and EXPLAIN died on alias
    ``i``. No FROM -> None (caller retries / abstains).
    """
    if not text:
        return None
    blobs: list[str] = [m.group(1).strip() for m in _SQL_FENCE.finditer(text)]
    blobs.append(_SQL_FENCE.sub(" ", text).strip())
    blobs.append(text.strip())
    for body in blobs:
        sql = _one_select(body)
        if sql and _FROM.search(sql):
            return sql
    return None


__all__ = ["extract_select"]
