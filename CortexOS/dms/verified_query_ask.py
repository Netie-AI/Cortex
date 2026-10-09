"""VERIFIED-QUERY (#309): load steward-confirmed examples for the SQL generator.

Off unless ``CORTEX_VERIFIED_QUERY`` is set. When off, :func:`generation_examples`
returns ``None`` before touching the library, and the historical phrase lanes
are left alone.

Which examples belong with the question is lexical similarity over the
confirmed questions. That is not an exact-phrase table. The chosen examples
are fed to the SQL generator, which writes new SQL. This module does not
execute stored SQL. When the flag is on, the ask does not also consult
``match_certified`` or ``route_to_metric``.
"""

from __future__ import annotations

import os
import re
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from CortexOS.memory.verified_query import get_verified_queries

ENABLED_ENV = "CORTEX_VERIFIED_QUERY"
ASK_ACTOR = "cortex:contract-ask"
# Lexical similarity over the token union. Not an exact-phrase branch.
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


def generation_examples(
    *,
    space_id: str,
    question: str,
    scored_pack_id: str | None = None,
) -> dict[str, Any] | None:
    """Prompt examples for one Space, or ``None`` when off, empty, or not retrieved.

    Lexical similarity names the ids. A question that is not near any
    confirmed question gets no examples. ``verified_query_id`` names the
    retrieved examples. Memory fields name those C-MEM solutions.
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
        "prompt": prompt,
        "verified_query_id": ",".join(item["id"] for item in prompt),
        "memory_ids_read": [ex.entry.id for ex in picked],
        "memory_reads": [
            {**ex.entry.provenance(), "served_at": ex.read_stamp.served_at} for ex in picked
        ],
    }


_PREAMBLE = (
    "VERIFIED EXAMPLES from this Space. They show question/SQL pairs a steward "
    "confirmed. Write a new SELECT for the QUESTION. An example is not the query "
    "to execute."
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


@dataclass(frozen=True, slots=True)
class _AskBind:
    space_id: str
    scored_pack_id: str | None


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
    """L2 port wrapper. Examples go into the question string the generator prompts with."""

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
            )
            if meta is not None:
                asked = question_with_examples(question, list(meta["prompt"]))
                passed = dict(schema or {})
                passed["verified_examples"] = meta["prompt"]
                _USED.set(meta)
        return self._inner.generate_candidates(
            asked, passed, prior_violations=prior_violations
        )


def bind_verified_examples(
    *, space_id: str, scored_pack_id: str | None
) -> tuple[Token[Any], Token[Any], Any, bool]:
    """Arm example loading for this ask. The L2 port is restored when the ask ends.

    A test that stubs ``resolve_l2_generation`` is left alone: its stub is not
    installed as the process port.
    """
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
        return (
            _ASK.set(_AskBind(space_id=space_id, scored_pack_id=scored_pack_id)),
            _USED.set(None),
            restore,
            phrase,
        )
    except Exception:
        _resume_exact_phrase_match(phrase)
        raise


def take_verified_examples(
    tokens: tuple[Token[Any], Token[Any], Any, bool] | None,
) -> dict[str, Any] | None:
    """Return examples that were placed in a prompt, and put the L2 port back."""
    meta = _USED.get()
    if tokens is not None:
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
    note = (
        f"verified examples {meta['verified_query_id']} were in the SQL prompt. "
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
    data["verified_query_id"] = meta["verified_query_id"]
    data["memory_ids_read"] = list(meta["memory_ids_read"])
    data["memory_reads"] = list(meta["memory_reads"])
    data["reused"] = False
