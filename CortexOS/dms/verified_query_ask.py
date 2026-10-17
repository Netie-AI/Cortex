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
   sends. The model writes the SELECT. This module never executes stored SQL.
3. **Check before serving.** :func:`example_echo` compares the SQL that ran
   with the examples that were in the prompt. SQL that carries an example's
   value, filter or whole query into an answer for a question that does not
   ask for it is refused with ``VQ_EXAMPLE_ECHO``, and the ask abstains. The
   refusal runs in main's plausibility step, so the answer is not served,
   promoted or remembered.

When the flag is on, the ask does not also consult ``match_certified`` or
``route_to_metric``.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Collection, Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

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
    """Canonical text with the row cap removed, so a copy plus ``LIMIT`` is still a copy."""
    copy = tree.copy()
    if "limit" in copy.args:
        copy.set("limit", None)
    try:
        return copy.sql(dialect="duckdb", normalize=True, pretty=False)
    except Exception:  # noqa: BLE001
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
    and writes a new SELECT. The example SQL is not executed.
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
@dataclass(frozen=True, slots=True)
class _Seen:
    id: str
    words: frozenset[str]
    literals: frozenset[str]
    valueless: frozenset[str]
    key: str | None


def _seen(examples: Iterable[Mapping[str, Any]]) -> list[_Seen]:
    out: list[_Seen] = []
    for item in examples:
        tree = _parse(str(item.get("sql") or ""))
        if tree is None:
            continue
        out.append(
            _Seen(
                id=str(item.get("id") or ""),
                words=_tokens(str(item.get("question") or "")),
                literals=frozenset(_comparison_literals(tree)),
                valueless=frozenset(_valueless_filter_tokens(tree)),
                key=_query_key(tree),
            )
        )
    return out


def example_echo(
    question: str,
    sql: str,
    examples: Iterable[Mapping[str, Any]],
    *,
    schema: Mapping[str, Collection[str]] | None = None,
) -> list[str]:
    """Named refusals when ``sql`` answers a retrieved example instead of ``question``.

    Empty means serve. Each entry starts with :data:`ECHO_CODE`. Three rules,
    all fail-closed:

    * **value**: a value the SQL compares against that is also in an example's
      SQL must be spelled in the question. The one exception is the question
      the steward confirmed, word for word: that is a re-serve with a fresh
      execution. ``status = 'DELAYED'`` for "pending shipments" is refused.
    * **dropped filter**: when the SQL keeps a filter that has no value
      (``is_hazardous = true``) from an example, the question must still say
      the word the example's question used for it.
    * **copy**: when the SQL is an example's SQL (row cap aside) and the
      question is not that example's question, a word the question adds must
      not be a number the SQL lacks, or name a table or a column of a table
      the SQL reads that the SQL does not use. "How many locations" answered
      with ``COUNT(DISTINCT sku)`` is refused.

    SQL that cannot be parsed is refused too.
    """
    seen = _seen(examples)
    if not seen:
        return []
    tree = _parse(sql)
    if tree is None:
        return [f"{ECHO_CODE}: the SQL that ran cannot be compared with the verified examples"]
    asked = _tokens(question)
    out: list[str] = []

    for value in sorted(_comparison_literals(tree)):
        sources = [ex for ex in seen if value in ex.literals]
        if not sources or any(ex.words == asked for ex in sources):
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
    copies = [ex for ex in seen if key is not None and ex.key == key and ex.words != asked]
    if copies:
        layout = current_schema() if schema is None else schema
        reads = _tables(tree)
        uses = reads | _columns(tree)
        used_words = {tok for name in uses for tok in _name_tokens(name)}
        used_words |= {tok for lit in tree.find_all(exp.Literal) for tok in _TOKEN.findall(_literal_text(lit))}
        fields: set[str] = {t for t in layout if t not in reads}
        for table in reads:
            for col in layout.get(table, ()):
                if col in uses:
                    continue
                head = next((tok for tok in _name_tokens(col) if len(tok) >= 3), "")
                if head:
                    fields.add(head)
        for ex in copies:
            for word in sorted(asked):
                if _touches_any(word, ex.words) or _touches_any(word, used_words):
                    continue
                if any(ch.isdigit() for ch in word):
                    why = f"the number '{word}'"
                elif _touches_any(word, fields):
                    why = f"'{word}'"
                else:
                    continue
                out.append(
                    f"{ECHO_CODE}: the SQL is verified example {ex.id} unchanged, and the "
                    f"question asks about {why}, which that SQL does not use"
                )
    return out


@dataclass(frozen=True, slots=True)
class _AskBind:
    space_id: str
    scored_pack_id: str | None
    granted_tables: frozenset[str] | None


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
    is served, and abstains on anything it returns.
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
            found.extend(
                example_echo(
                    str(meta.get("question") or ""),
                    sql,
                    list(meta.get("prompt") or []),
                    schema=meta.get("schema"),
                )
            )
        return found


_Tokens = tuple[Token[Any], Token[Any], Any, bool]


def bind_verified_examples(
    *,
    space_id: str,
    scored_pack_id: str | None,
    granted_tables: Collection[str] | None = None,
) -> _Tokens | None:
    """Arm example loading for this ask. ``None`` when the flag is off.

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
                _AskBind(space_id=space_id, scored_pack_id=scored_pack_id, granted_tables=grant)
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
