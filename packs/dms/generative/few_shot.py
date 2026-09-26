"""CX-FS-01 — dynamic few-shot examples for L2 Text2SQL.

Verified ``(question, sql)`` pairs come from two places only:

* certified gold in ``packs/dms/semantic/certified_queries.yaml`` — the same
  file ``CortexOS.crew.cot_climb.certified_gold_sql`` reads (the pack reads its
  own YAML; it never imports ``CortexOS.crew``);
* steward-**approved** L2 promotions in ``promotion.py``'s queue. Pending and
  rejected (never approved) pairs are excluded.

This lives in the pack, so the engine never imports it (C2). Selection is
fail-closed on reach: an example is only offered when every table it reads is
in the reduced schema the session was retrieved, so an example can never steer
the model to a table the prompt did not grant.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from packs.dms.generative import promotion
from packs.dms.semantic.loader import CERTIFIED_PATH, stated_tables

#: A lexical Jaccard below this is "unrelated": no examples beat bad examples.
SCORE_FLOOR = 0.15
#: Ranking bonus for sharing tables with the retrieved schema (ranking only).
TABLE_BONUS = 0.1
DEFAULT_K = 3


@dataclass(frozen=True, slots=True)
class Example:
    question: str
    sql: str
    tables: tuple[str, ...]
    source: str  # "certified" | "promoted"
    phrasings: tuple[str, ...] = field(default=())


@dataclass
class _Cache:
    key: tuple[Any, ...] = ()
    examples: list[Example] = field(default_factory=list)


_CACHE = _Cache()

_STOP = frozenset(
    """a an the of in on at to for by and or is are was were be been do does did
    we our us you your i me my it its this that these those what which who whom
    how show list give tell me all any please with from across per each there
    have has had can could would should will get""".split()
)
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
_TOKEN = re.compile(r"[a-z0-9]+")


def _lemma(tok: str) -> str:
    """Cheap stdlib lemmatiser: number words, plural and verb suffixes."""
    tok = _NUMBER_WORDS.get(tok, tok)
    if len(tok) <= 3 or tok.isdigit():
        return tok
    for suffix, repl in (
        ("ies", "y"),
        ("sses", "ss"),
        ("ing", ""),
        ("ed", ""),
        ("es", ""),
        ("s", ""),
    ):
        if tok.endswith(suffix) and len(tok) - len(suffix) >= 3:
            if suffix == "s" and tok.endswith("ss"):
                continue
            return tok[: -len(suffix)] + repl
    return tok


def question_tokens(text: str) -> frozenset[str]:
    return frozenset(
        _lemma(t) for t in _TOKEN.findall((text or "").lower()) if t not in _STOP
    )


def normalize_sql(sql: str) -> str:
    """Whitespace/case/semicolon fold for dedupe (same fold as cot_climb)."""
    return " ".join((sql or "").replace(";", " ").split()).strip().lower()


def sql_tables(sql: str) -> tuple[str, ...]:
    """Base tables an SQL reads (CTE names excluded). Falls back to FROM/JOIN regex."""
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="duckdb")
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        seen: list[str] = []
        for tbl in tree.find_all(exp.Table):
            name = (tbl.name or "").lower()
            if name and name not in ctes and name not in seen:
                seen.append(name)
        if seen:
            return tuple(seen)
    except Exception:  # noqa: BLE001 - fall back to the loader's stated tables
        pass
    return stated_tables(sql)


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _certified_rows(path: Path) -> list[tuple[str, str, tuple[str, ...]]]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    rows: list[tuple[str, str, tuple[str, ...]]] = []
    for row in list(doc.get("certified") or []) + list(doc.get("queries") or []):
        if not isinstance(row, dict):
            continue
        q = str(row.get("question") or "").strip()
        sql = " ".join(str(row.get("sql") or "").split())
        if q and sql:
            syn = tuple(str(s).strip() for s in (row.get("synonyms") or []) if str(s).strip())
            rows.append((q, sql, syn))
    return rows


def _approved_rows(path: Path) -> list[tuple[str, str, tuple[str, ...]]]:
    """Steward-approved promotions only. Read-only; never creates the DB."""
    if not path.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT question, sql_text FROM l2_usage WHERE approved = 1"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for q, sql in rows:
        q = str(q or "").strip()
        sql = " ".join(str(sql or "").split())
        if q and sql:
            out.append((q, sql, ()))
    return out


def load_examples(
    *,
    certified_path: Path | None = None,
    promotion_db: Path | None = None,
) -> list[Example]:
    """All verified pairs, deduplicated by normalised SQL. Cached by source mtimes."""
    global _CACHE
    cert = Path(certified_path or CERTIFIED_PATH)
    prom = Path(promotion_db or promotion.DEFAULT_DB)
    key = (str(cert), _mtime(cert), str(prom), _mtime(prom))
    if _CACHE.key == key:
        return list(_CACHE.examples)

    seen: set[str] = set()
    out: list[Example] = []
    sources = (("certified", _certified_rows(cert)), ("promoted", _approved_rows(prom)))
    for source, rows in sources:
        for q, sql, syn in rows:
            norm = normalize_sql(sql)
            if norm in seen:
                continue
            seen.add(norm)
            out.append(
                Example(
                    question=q,
                    sql=sql,
                    tables=sql_tables(sql),
                    source=source,
                    phrasings=(q, *syn),
                )
            )
    _CACHE = _Cache(key=key, examples=out)
    return list(out)


def invalidate() -> None:
    global _CACHE
    _CACHE = _Cache()


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_examples(
    question: str,
    k: int = DEFAULT_K,
    tables: Iterable[str] | None = None,
    *,
    certified_path: Path | None = None,
    promotion_db: Path | None = None,
) -> list[tuple[str, str]]:
    """Top-k ``(question, sql)`` by lemma Jaccard + table-overlap bonus.

    ``tables`` is the reduced schema. When given, an example is eligible only if
    **every** table it reads is in it. The floor applies to the lexical score so
    a table bonus alone can never promote an unrelated example. ``[]`` when
    nothing clears the floor.
    """
    q_tokens = question_tokens(question)
    if not q_tokens or k <= 0:
        return []
    allowed = {str(t).lower() for t in tables} if tables is not None else None
    scored: list[tuple[float, float, int, Example]] = []
    for idx, ex in enumerate(
        load_examples(certified_path=certified_path, promotion_db=promotion_db)
    ):
        if not ex.tables:
            continue
        if allowed is not None and not set(ex.tables) <= allowed:
            continue
        lexical = max(_jaccard(q_tokens, question_tokens(p)) for p in ex.phrasings)
        if lexical < SCORE_FLOOR:
            continue
        bonus = 0.0
        if allowed:
            bonus = TABLE_BONUS * len(set(ex.tables) & allowed) / len(allowed)
        scored.append((lexical + bonus, lexical, -idx, ex))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [(ex.question, ex.sql) for *_, ex in scored[:k]]


__all__ = [
    "Example",
    "SCORE_FLOOR",
    "invalidate",
    "load_examples",
    "normalize_sql",
    "question_tokens",
    "select_examples",
    "sql_tables",
]
