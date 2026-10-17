"""VERIFIED-QUERY (#309): steward-confirmed examples in the SQL generator's context.

Off unless ``CORTEX_VERIFIED_QUERY`` is set. When off, the ask binds nothing,
:func:`generation_examples` returns ``None`` before touching the library, and
the historical phrase lanes are left alone.

Three steps, all in this module:

1. **Retrieve.** Only examples whose tables are inside the session's grant and
   whose tables and columns still exist in the semantic layer are candidates.
   Lexical similarity over the confirmed questions orders them. That is not an
   exact-phrase table, and it never picks an answer: it decides which pairs
   the model reads.
2. **Generate.** The chosen pairs go into the prompt the SQL generator already
   sends. The model writes the SELECT. An example's SQL never produces the
   answer; it is executed only by the serve-time result comparison below.
3. **Check before serving.** :func:`example_echo` compares the SQL that ran
   with the examples that were in the prompt, and :func:`result_echo`
   compares the rows. An example is context for new SQL, never the answer to
   another question:

   * an example's SQL that runs unchanged (row cap and names aside) is served
     only for the question the steward confirmed with it, compared as C-MEM's
     normalised question key, and only while that example is still inside
     the session grant and the current schema. That is the one re-serve, and
     it is a fresh execution. Any other question that runs it abstains;
   * SQL written differently that returns an example's rows (column names,
     column order and row order aside) is that example's answer too, and is
     held to the same re-serve rule. This closes the equivalent rewrites the
     text comparison cannot see (``ORDER BY 2``, ``'X' = col``,
     ``col IN ('X')``, reordered conjuncts or columns, an added ``ORDER BY``);
   * SQL that carries an example's value or value-less filter into an answer
     for a question that does not ask for it abstains too.

   Each refusal is ``VQ_EXAMPLE_ECHO``. It runs in main's plausibility step,
   so a refused answer is not served, promoted or remembered.

When the flag is on, the ask does not also consult ``match_certified`` or
``route_to_metric``.
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.dms.sql_guardrail import MAX_LIMIT
from CortexOS.memory.space_memory import question_key
from CortexOS.memory.verified_query import get_verified_queries

ENABLED_ENV = "CORTEX_VERIFIED_QUERY"
ASK_ACTOR = "cortex:contract-ask"
ECHO_CODE = "VQ_EXAMPLE_ECHO"

# Share of the token union two questions must have before the confirmed one
# is put in the prompt. This is a prompt-budget filter, not a safety control:
# it only decides which pairs the model reads. Whatever it admits, the
# serve-time check (example_echo) refuses SQL that answers the example's
# question instead of this one. Set just under 0.42, the score of the
# paraphrase pair in the unit must-fail
#   test_must_fail_paraphrase_of_certified_question_reaches_the_example
# It was not tuned on a scored pack.
RETRIEVE_MIN = 0.35
_TOKEN = re.compile(r"[a-z0-9]+")

_TRUTHY = {"1", "true", "on", "yes"}


def verified_query_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in _TRUTHY


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall((text or "").lower()))


def lexical_similarity(left: str, right: str) -> float:
    """Share of the token union. Not an exact-phrase lookup."""
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def retrieve_example_ids(question: str, candidates: list[tuple[str, str]]) -> list[str]:
    """Ids whose question is lexically near ``question``, best score first.

    ``candidates`` is ``(id, question)``. A score under :data:`RETRIEVE_MIN`
    is not an example. There is no normalized-phrase dict and no equality
    shortcut. At most :data:`EXAMPLE_CAP` ids are returned.
    """
    from CortexOS.memory.verified_query import EXAMPLE_CAP

    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for qid, text in candidates:
        key = str(qid).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        score = lexical_similarity(question, text)
        if score >= RETRIEVE_MIN:
            scored.append((score, key))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [qid for _score, qid in scored[:EXAMPLE_CAP]]


# -- SQL facts used by the scope filter and the serve-time check ---------------
def _parse(sql: str) -> exp.Expression | None:
    try:
        trees = [tree for tree in sqlglot.parse(sql or "", read="duckdb") if tree is not None]
    except Exception:  # noqa: BLE001 - unparseable SQL is never an example
        return None
    return trees[0] if len(trees) == 1 else None


def _name_tokens(name: str) -> list[str]:
    return _TOKEN.findall((name or "").lower())


def _cte_names(tree: exp.Expression) -> set[str]:
    return {str(cte.alias or "").lower() for cte in tree.find_all(exp.CTE) if cte.alias}


def _tables(tree: exp.Expression) -> set[str]:
    names = {str(t.name or "").lower() for t in tree.find_all(exp.Table) if t.name}
    return names - _cte_names(tree)


def _columns(tree: exp.Expression) -> set[str]:
    return {str(c.name or "").lower() for c in tree.find_all(exp.Column) if c.name}


def _aliases(tree: exp.Expression) -> set[str]:
    named = {str(a.alias or "").lower() for a in tree.find_all(exp.Alias) if a.alias}
    return named | _cte_names(tree)


def _literal_text(lit: exp.Literal) -> str:
    return str(lit.this or "").strip().lower()


def _predicate_values(pred: exp.Expression) -> set[str]:
    """Literals on the value side of one comparison.

    ``status = 'DELAYED'`` gives ``delayed``; ``100.0 * load / cap > 90`` gives
    ``90`` only, since a constant inside the column expression is not a value
    the question chooses.
    """
    if isinstance(pred, exp.In):
        sides: list[Any] = list(pred.expressions)
    elif isinstance(pred, exp.Between):
        sides = [pred.args.get("low"), pred.args.get("high")]
    elif isinstance(pred, exp.Binary):
        sides = [pred.left, pred.right]
    else:
        sides = [pred]
    return {
        text
        for side in sides
        if isinstance(side, exp.Expression) and side.find(exp.Column) is None
        for lit in side.find_all(exp.Literal)
        if (text := _literal_text(lit))
    }


def _comparison_literals(tree: exp.Expression) -> set[str]:
    """Values the query compares against: ``status = 'DELAYED'``, ``x > 90``."""
    return {text for pred in tree.find_all(exp.Predicate) for text in _predicate_values(pred)}


def _valueless_filter_tokens(tree: exp.Expression) -> set[str]:
    """Words of the columns in filters that compare against no value (``is_hazardous = true``).

    A filter with a value is held to the value rule instead.
    """
    out: set[str] = set()
    for pred in tree.find_all(exp.Predicate):
        if _predicate_values(pred):
            continue
        for col in pred.find_all(exp.Column):
            out.update(tok for tok in _name_tokens(col.name) if len(tok) >= 3)
    return out


def _query_key(tree: exp.Expression) -> str | None:
    """Canonical text of a query for the copy rule, or ``None`` if it cannot be made.

    Set aside: the top-level row cap (the serve path appends ``LIMIT``),
    output names (a reference to one in ``ORDER BY``/``HAVING``/``QUALIFY``/
    ``GROUP BY`` is replaced by the expression it names), table aliases,
    column qualifiers, identifier quoting and case. So a copy that only
    renames or re-caps is still a copy. Setting these aside can only make two
    different queries look alike, which refuses more, never less.
    """
    try:
        copy = tree.copy()
        if "limit" in copy.args:
            copy.set("limit", None)
        for table in copy.find_all(exp.Table):
            if table.alias:
                table.set("alias", None)
        for col in copy.find_all(exp.Column):
            if col.table:
                col.set("table", None)
        for select in list(copy.find_all(exp.Select)):
            named = {
                str(proj.alias).lower(): proj.this
                for proj in select.expressions
                if isinstance(proj, exp.Alias) and proj.alias
            }
            for arg in ("order", "having", "qualify", "group"):
                node = select.args.get(arg)
                if node is None:
                    continue
                for col in list(node.find_all(exp.Column)):
                    target = named.get(str(col.name or "").lower())
                    if target is not None:
                        col.replace(target.copy())
            select.set(
                "expressions",
                [p.this if isinstance(p, exp.Alias) else p for p in select.expressions],
            )
        for ident in copy.find_all(exp.Identifier):
            ident.set("quoted", False)
        return copy.sql(dialect="duckdb", normalize=True, pretty=False)
    except Exception:  # noqa: BLE001 - no key means no proof it is not a copy
        return None


def _touch(word: str, term: str) -> bool:
    """Same word, or one is the other plus a short ending (``sku``/``skus``).

    Numbers must match exactly. No word list: this is spelling only.
    """
    if word == term:
        return True
    if any(ch.isdigit() for ch in word + term):
        return False
    short, long_ = sorted((word, term), key=len)
    return len(short) >= 3 and long_.startswith(short) and len(long_) - len(short) <= 2


def _touches_any(word: str, terms: Iterable[str]) -> bool:
    return any(_touch(word, term) for term in terms)


# -- step 1: which examples may be read at all --------------------------------
def current_schema() -> dict[str, frozenset[str]]:
    """Tables and their columns as the semantic layer states them now."""
    from CortexOS.dms.warehouse_db import load_semantic_layer

    layer = load_semantic_layer() or {}
    tables = layer.get("tables") or {}
    return {
        str(name).lower(): frozenset(str(col).lower() for col in (spec or {}).get("columns") or [])
        for name, spec in tables.items()
        if isinstance(spec, dict)
    }


def example_fits(
    sql: str,
    *,
    granted_tables: Collection[str] | None,
    schema: Mapping[str, Collection[str]],
) -> bool:
    """True when the example reads only granted tables and current columns.

    An example whose table left the grant, or whose table or column is gone
    from the semantic layer, is not put in front of the model. Unparseable SQL
    is out.
    """
    tree = _parse(sql)
    if tree is None:
        return False
    tables = _tables(tree)
    if not tables:
        return False
    if granted_tables is not None and not tables <= {t.lower() for t in granted_tables}:
        return False
    if not tables <= set(schema):
        return False
    known: set[str] = set()
    for table in tables:
        known.update(schema[table])
    return _columns(tree) <= known | _aliases(tree)


def generation_examples(
    *,
    space_id: str,
    question: str,
    scored_pack_id: str | None = None,
    granted_tables: Collection[str] | None = None,
    schema: Mapping[str, Collection[str]] | None = None,
) -> dict[str, Any] | None:
    """Prompt examples for one Space, or ``None`` when off, empty, or not retrieved.

    Only examples inside the grant and the current schema are scored. Lexical
    similarity names the ids. A question that is not near any of them gets no
    examples. ``verified_query_ids`` lists the retrieved examples, best first.
    Memory fields name those C-MEM solutions.
    """
    if not verified_query_enabled():
        return None
    space = (space_id or "").strip()
    if not space:
        return None
    found = get_verified_queries().examples(
        space_id=space, actor=ASK_ACTOR, scored_pack_id=scored_pack_id
    )
    if not found:
        return None
    layout = current_schema() if schema is None else schema
    found = [
        ex
        for ex in found
        if example_fits(ex.query.sql, granted_tables=granted_tables, schema=layout)
    ]
    chosen = retrieve_example_ids(
        question, [(ex.query.id, ex.query.question) for ex in found]
    )
    by_id = {ex.query.id: ex for ex in found}
    picked = [by_id[qid] for qid in chosen if qid in by_id]
    if not picked:
        return None
    prompt = [
        {"id": ex.query.id, "question": ex.query.question, "sql": ex.query.sql} for ex in picked
    ]
    return {
        "question": question,
        "schema": {table: sorted(cols) for table, cols in layout.items()},
        "granted_tables": (
            None if granted_tables is None else sorted({str(t).lower() for t in granted_tables})
        ),
        "prompt": prompt,
        "verified_query_ids": [item["id"] for item in prompt],
        "memory_ids_read": [ex.entry.id for ex in picked],
        "memory_reads": [
            {**ex.entry.provenance(), "served_at": ex.read_stamp.served_at} for ex in picked
        ],
    }


_PREAMBLE = (
    "VERIFIED EXAMPLES from this Space. They show question/SQL pairs a steward "
    "confirmed. Write a new SELECT for the QUESTION. An example is not the query "
    "to execute. Take every filter value from the QUESTION, never from an example."
)


def question_with_examples(question: str, examples: list[dict[str, Any]]) -> str:
    """Put examples in front of the question the SQL generator already sends.

    The generator is unchanged. It copies this string into the FreeRoute prompt
    and writes a new SELECT. The example SQL never produces the answer; only
    the serve-time result comparison (:func:`result_echo`) runs it.
    """
    lines = [_PREAMBLE]
    for item in examples:
        qid = str(item.get("id") or "").strip()
        asked = str(item.get("question") or "").strip()
        sql = str(item.get("sql") or "").strip()
        if not asked or not sql:
            continue
        lines.append(f"EXAMPLE {qid}:")
        lines.append(f"Question: {asked}")
        lines.append(f"SQL: {sql}")
    if len(lines) == 1:
        return question
    lines.append("")
    lines.append(question)
    return "\n".join(lines)


# -- step 3: the SQL that ran must answer this question, not an example's -----
def same_question(asked: str, confirmed: str) -> bool:
    """True when ``asked`` is the question the steward confirmed.

    The comparison is C-MEM's solution key (:func:`question_key`): case,
    spacing and end punctuation aside, every word in its place and every
    symbol kept. "above 300 ... below 10" is not "below 300 ... above 10",
    and "> 300" is not "< 300". It is applied only to an example retrieval
    already put in the prompt, and only to decide whether that example's SQL
    may be re-served. It never chooses an example or an answer.
    """
    key = question_key(asked or "")
    return bool(key) and key == question_key(confirmed or "")


@dataclass(frozen=True, slots=True)
class _Seen:
    id: str
    question: str
    sql: str
    words: frozenset[str]
    literals: frozenset[str]
    valueless: frozenset[str]
    key: str | None
    tables: frozenset[str]


def _seen(examples: Iterable[Mapping[str, Any]]) -> list[_Seen]:
    out: list[_Seen] = []
    for item in examples:
        sql = str(item.get("sql") or "")
        tree = _parse(sql)
        if tree is None:
            continue
        question = str(item.get("question") or "")
        out.append(
            _Seen(
                id=str(item.get("id") or ""),
                question=question,
                sql=sql,
                words=_tokens(question),
                literals=frozenset(_comparison_literals(tree)),
                valueless=frozenset(_valueless_filter_tokens(tree)),
                key=_query_key(tree),
                tables=frozenset(_tables(tree)),
            )
        )
    return out


def _re_serve_test(
    question: str,
    schema: Mapping[str, Collection[str]] | None,
    granted_tables: Collection[str] | None,
) -> Callable[[_Seen], bool]:
    """``ex -> True`` when this ask is a re-serve of example ``ex``.

    The question is the example's confirmed question (:func:`same_question`),
    and the example still reads only granted tables and current columns
    (:func:`example_fits`). The schema is read once, and only if needed.
    """
    layout_box: list[Mapping[str, Collection[str]]] = []

    def re_serve(ex: _Seen) -> bool:
        if not same_question(question, ex.question):
            return False
        if not layout_box:
            layout_box.append(current_schema() if schema is None else schema)
        return example_fits(ex.sql, granted_tables=granted_tables, schema=layout_box[0])

    return re_serve


def example_echo(
    question: str,
    sql: str,
    examples: Iterable[Mapping[str, Any]],
    *,
    schema: Mapping[str, Collection[str]] | None = None,
    granted_tables: Collection[str] | None = None,
) -> list[str]:
    """Named refusals when ``sql`` answers a retrieved example instead of ``question``.

    Empty means serve. Each entry starts with :data:`ECHO_CODE`. All rules are
    fail-closed.

    A **re-serve** is the one case where an example's own SQL may answer: the
    question is the example's confirmed question (:func:`same_question`), and
    the example still reads only tables in ``granted_tables`` and tables and
    columns in ``schema`` (:func:`example_fits`). The SQL is executed fresh.

    * **copy**: SQL that is an example's SQL (row cap and names aside) is
      refused unless this ask is a re-serve of that example. No word in the
      question can make a copy acceptable for another question: a near miss
      ("pending" for "delayed", "lowest" for "highest", "inbound", "not",
      reordered words, a paraphrase) abstains.
    * **value**: a value the SQL compares against that is also in an
      example's SQL must be spelled in the question, unless this ask is a
      re-serve of that example. ``status = 'DELAYED'`` for "pending
      shipments" is refused even when the SQL is not a copy.
    * **dropped filter**: when the SQL keeps a filter that has no value
      (``is_hazardous = true``) from an example, the question must still say
      the word the example's question used for it.

    SQL that cannot be parsed or canonicalised is refused too. SQL that is
    written differently but returns an example's rows is :func:`result_echo`'s
    to refuse.
    """
    seen = _seen(examples)
    if not seen:
        return []
    tree = _parse(sql)
    if tree is None:
        return [f"{ECHO_CODE}: the SQL that ran cannot be compared with the verified examples"]
    asked = _tokens(question)
    re_serve = _re_serve_test(question, schema, granted_tables)

    out: list[str] = []

    for value in sorted(_comparison_literals(tree)):
        sources = [ex for ex in seen if value in ex.literals]
        if not sources or any(re_serve(ex) for ex in sources):
            continue
        parts = _TOKEN.findall(value)
        if parts and all(_touches_any(part, asked) for part in parts):
            continue
        out.append(
            f"{ECHO_CODE}: the value '{value}' comes from verified example "
            f"{sources[0].id} and the question does not ask for it"
        )

    kept = _valueless_filter_tokens(tree)
    for ex in seen:
        shared = kept & ex.valueless
        if not shared:
            continue
        for word in sorted(ex.words):
            if _touches_any(word, asked):
                continue
            term = next((t for t in sorted(shared) if _touch(word, t)), None)
            if term is not None:
                out.append(
                    f"{ECHO_CODE}: the SQL keeps the '{term}' filter of verified example "
                    f"{ex.id}, whose question says '{word}', and this question does not"
                )

    key = _query_key(tree)
    if key is None:
        out.append(f"{ECHO_CODE}: the SQL that ran cannot be compared with the verified examples")
        return out
    copied = [ex for ex in seen if ex.key is None or ex.key == key]
    if copied and not any(re_serve(ex) for ex in copied):
        for ex in copied:
            why = (
                "but that example no longer fits this session's grant and the current schema"
                if same_question(question, ex.question)
                else "and only the question the steward confirmed for it may re-serve it"
            )
            out.append(
                f"{ECHO_CODE}: the SQL is verified example {ex.id} (row cap and names aside), {why}"
            )
    return out


# -- step 3, by result: the rows served must not be an example's rows ----------
RunSql = Callable[[str], list[dict[str, Any]]]


def _cell(value: Any) -> str:
    """One value as the result comparison sees it.

    Numbers compare by value to ten significant digits (``1031`` is
    ``1031.0``), text by its trimmed, case-folded spelling. Setting these
    aside can only make two results look alike, which refuses more, never
    less.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return str(value)
        if math.isnan(number):
            return "nan"
        return format(number, ".10g")
    return str(value).strip().casefold()


def _rows_key(rows: Iterable[Mapping[str, Any]]) -> Counter[tuple[str, ...]]:
    """Rows as a multiset, with column names, column order and row order set aside."""
    return Counter(tuple(sorted(_cell(v) for v in row.values())) for row in rows)


def result_echo(
    question: str,
    sql: str,
    examples: Iterable[Mapping[str, Any]],
    *,
    run: RunSql,
    schema: Mapping[str, Collection[str]] | None = None,
    granted_tables: Collection[str] | None = None,
) -> list[str]:
    """Named refusals when the rows ``sql`` returns are a retrieved example's rows.

    :func:`example_echo` compares SQL text, so an equivalent rewrite of an
    example (``ORDER BY 2``, ``'X' = col``, ``col IN ('X')``, conjuncts or
    output columns in another order, an added ``ORDER BY``) is new SQL to it.
    This rule compares what the customer would get. ``run`` executes one
    statement the way the serve path does, under this session's manifest.

    Each example in the prompt that reads a table ``sql`` reads, and that this
    ask is not a re-serve of, runs next to ``sql``. When the two results are
    the same multiset of rows (:func:`_rows_key`: column names, column order
    and row order aside), the answer is that example's answer and is refused.
    It is served only when a re-serve of an example returns the same rows,
    since two confirmed questions may share one answer. Fail-closed:

    * ``sql`` or an example that cannot be run cannot be shown to differ, so
      the answer is refused;
    * two results that both fill the serve path's row cap (``MAX_LIMIT``)
      cannot be shown to differ past it, so the answer is refused;
    * a different question whose right answer happens to equal an example's
      rows abstains too. That is the price of never serving a near miss.

    Nothing runs when ``sql`` is a re-serve's own SQL (row cap and names
    aside), or when no other example shares a table with it. An example's
    rows are compared here and never served.
    """
    seen = _seen(examples)
    if not seen:
        return []
    tree = _parse(sql)
    if tree is None:
        return [f"{ECHO_CODE}: the SQL that ran cannot be compared with the verified examples"]
    read = _tables(tree)
    re_serve = _re_serve_test(question, schema, granted_tables)
    served_by = [ex for ex in seen if re_serve(ex)]
    key = _query_key(tree)
    if key is not None and any(ex.key == key for ex in served_by):
        return []
    others = [ex for ex in seen if ex.tables & read and ex not in served_by]
    if not others:
        return []
    try:
        rows = run(sql)
    except Exception:  # noqa: BLE001 - rows that cannot be read cannot be shown to differ
        return [
            f"{ECHO_CODE}: the rows of the SQL that ran cannot be compared with "
            f"verified example {others[0].id}"
        ]
    answer = _rows_key(rows)
    cap = int(MAX_LIMIT)

    def verdict(ex: _Seen) -> str:
        try:
            theirs = run(ex.sql)
        except Exception:  # noqa: BLE001 - an example that cannot run is not shown to differ
            return "unrun"
        if len(rows) >= cap and len(theirs) >= cap:
            return "capped"
        return "same" if _rows_key(theirs) == answer else "differ"

    out: list[str] = []
    for ex in others:
        found = verdict(ex)
        if found == "same":
            out.append(
                f"{ECHO_CODE}: the SQL returns the rows of verified example {ex.id} "
                "(columns and order aside), and only the question the steward "
                "confirmed for it may re-serve them"
            )
        elif found == "capped":
            out.append(
                f"{ECHO_CODE}: the SQL and verified example {ex.id} both fill the "
                f"{cap}-row cap, so their rows cannot be shown to differ"
            )
        elif found == "unrun":
            out.append(
                f"{ECHO_CODE}: verified example {ex.id} could not be run to show its "
                "rows differ from this answer"
            )
    if out and any(verdict(ex) == "same" for ex in served_by if ex.tables & read):
        return []
    return out


def _session_runner(verified: Any) -> RunSql:
    """Run one statement as the contract ask serves one: SQL gate, then the manifest."""

    def run(statement: str) -> list[dict[str, Any]]:
        if verified is None:
            raise RuntimeError("no session manifest to run the comparison under")
        from CortexOS.dms.sql_validate_gate import run_gate
        from CortexOS.dms.warehouse_db import load_semantic_layer
        from CortexOS.execution import submit

        gate = run_gate(statement, load_semantic_layer())
        if not gate.passed or not gate.safe_sql:
            raise RuntimeError(f"SQL gate refused the statement: {gate.violations}")
        rows, _queue_ms, _exec_ms = submit.execute_sql(verified, gate.safe_sql)
        return list(rows)

    return run


@dataclass(frozen=True, slots=True)
class _AskBind:
    space_id: str
    scored_pack_id: str | None
    granted_tables: frozenset[str] | None
    verified: Any = None


_ASK: ContextVar[_AskBind | None] = ContextVar("verified_query_ask", default=None)
_USED: ContextVar[dict[str, Any] | None] = ContextVar("verified_query_used", default=None)
_PHRASE_LOCK = threading.Lock()
_PHRASE_DEPTH = 0
_PHRASE_SAVED: list[tuple[Any, str, Any]] | None = None


def _no_phrase_match(*_args: Any, **_kwargs: Any) -> None:
    """Stand-in so the exact-phrase and metric-phrase lanes do not also run."""
    return None


def _suspend_exact_phrase_match() -> bool:
    """While the flag is on, do not also serve from the phrase tables.

    ``match_certified`` is an exact normalized-phrase dict. ``route_to_metric``
    is the keyword cascade. Retrieval into the generator replaces both for
    this ask. Flag off leaves them as they are.
    """
    global _PHRASE_DEPTH, _PHRASE_SAVED
    if not verified_query_enabled():
        return False
    import CortexOS.dms.answer_engine as answer_engine

    with _PHRASE_LOCK:
        if _PHRASE_DEPTH == 0:
            saved: list[tuple[Any, str, Any]] = []
            for name in ("match_certified", "route_to_metric"):
                saved.append((answer_engine, name, getattr(answer_engine, name)))
                setattr(answer_engine, name, _no_phrase_match)
            _PHRASE_SAVED = saved
        _PHRASE_DEPTH += 1
    return True


def _resume_exact_phrase_match(active: bool) -> None:
    global _PHRASE_DEPTH, _PHRASE_SAVED
    if not active:
        return
    with _PHRASE_LOCK:
        if _PHRASE_DEPTH <= 0:
            return
        _PHRASE_DEPTH -= 1
        if _PHRASE_DEPTH == 0 and _PHRASE_SAVED is not None:
            for mod, name, fn in _PHRASE_SAVED:
                setattr(mod, name, fn)
            _PHRASE_SAVED = None


class _ExamplePort:
    """L2 port wrapper.

    ``generate_candidates`` puts examples into the question string the
    generator prompts with. ``leftover_literals`` is the serve-time check:
    main's plausibility step calls it on the SQL that ran, before the answer
    is served, promoted or remembered, and abstains on anything it returns.
    The SQL rules (:func:`example_echo`) run first; when they pass, the rows
    are compared (:func:`result_echo`) under the session manifest the ask
    was bound with.
    """

    _vq_examples = True

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]:
        bound = _ASK.get()
        asked = question
        passed = schema
        if bound is not None:
            meta = generation_examples(
                space_id=bound.space_id,
                question=question,
                scored_pack_id=bound.scored_pack_id,
                granted_tables=bound.granted_tables,
            )
            if meta is not None:
                asked = question_with_examples(question, list(meta["prompt"]))
                passed = dict(schema or {})
                passed["verified_examples"] = meta["prompt"]
                _USED.set(meta)
        return self._inner.generate_candidates(
            asked, passed, prior_violations=prior_violations
        )

    def leftover_literals(self, sql: str) -> list[str]:
        inner = getattr(self._inner, "leftover_literals", None)
        found = [str(item) for item in (inner(sql) or [])] if callable(inner) else []
        meta = _USED.get()
        if meta is not None:
            question = str(meta.get("question") or "")
            prompt = list(meta.get("prompt") or [])
            echo = example_echo(
                question,
                sql,
                prompt,
                schema=meta.get("schema"),
                granted_tables=meta.get("granted_tables"),
            )
            if not echo:
                bound = _ASK.get()
                echo = result_echo(
                    question,
                    sql,
                    prompt,
                    run=_session_runner(None if bound is None else bound.verified),
                    schema=meta.get("schema"),
                    granted_tables=meta.get("granted_tables"),
                )
            found.extend(echo)
        return found


_Tokens = tuple[Token[Any], Token[Any], Any, bool]


def bind_verified_examples(
    *,
    space_id: str,
    scored_pack_id: str | None,
    granted_tables: Collection[str] | None = None,
    verified: Any = None,
) -> _Tokens | None:
    """Arm example loading for this ask. ``None`` when the flag is off.

    ``verified`` is the session's verified manifest. The serve-time result
    comparison runs under it; without it, an answer from a prompt that held
    examples cannot be compared and abstains.

    The L2 port is restored when the ask ends. A test that stubs
    ``resolve_l2_generation`` is left alone: its stub is not installed as the
    process port.
    """
    if not verified_query_enabled():
        return None
    from CortexOS.dms import l2_generation

    phrase = _suspend_exact_phrase_match()
    restore: Any = None
    try:
        try:
            port = l2_generation.resolve_l2_generation()
        except l2_generation.L2NotRegistered:
            port = None
        if (
            port is not None
            and port is l2_generation._port
            and not getattr(port, "_vq_examples", False)
        ):
            l2_generation.register_l2_generation(_ExamplePort(port))
            restore = port
        grant = (
            frozenset(str(t).lower() for t in granted_tables)
            if granted_tables is not None
            else None
        )
        return (
            _ASK.set(
                _AskBind(
                    space_id=space_id,
                    scored_pack_id=scored_pack_id,
                    granted_tables=grant,
                    verified=verified,
                )
            ),
            _USED.set(None),
            restore,
            phrase,
        )
    except Exception:
        _resume_exact_phrase_match(phrase)
        raise


def take_verified_examples(tokens: _Tokens | None) -> dict[str, Any] | None:
    """Return examples that were placed in a prompt, and put the L2 port back."""
    if tokens is None:
        return None
    meta = _USED.get()
    ask_token, used_token, restore, phrase = tokens
    _USED.reset(used_token)
    _ASK.reset(ask_token)
    if restore is not None:
        from CortexOS.dms.l2_generation import register_l2_generation

        register_l2_generation(restore)
    _resume_exact_phrase_match(phrase)
    return meta


def stamp_verified_examples(data: dict[str, Any], meta: dict[str, Any]) -> None:
    """Record that the generator saw these examples. Does not mark a stored answer."""
    ids = list(meta["verified_query_ids"])
    note = (
        f"verified examples {', '.join(ids)} were in the SQL prompt. "
        "The SQL was generated and executed on current data. Not a stored answer."
    )
    assumptions = data.get("assumptions")
    if isinstance(assumptions, str) and assumptions.strip():
        data["assumptions"] = assumptions.rstrip() + "; " + note
    elif isinstance(assumptions, list) and assumptions:
        first = str(assumptions[0]).rstrip()
        data["assumptions"] = [first + "; " + note, *assumptions[1:]]
    else:
        data["assumptions"] = note
    data["verified_query_ids"] = ids
    data["memory_ids_read"] = list(meta["memory_ids_read"])
    data["memory_reads"] = list(meta["memory_reads"])
    data["reused"] = False
