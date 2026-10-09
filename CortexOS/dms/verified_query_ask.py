"""VERIFIED-QUERY (#309): load steward-confirmed examples for the SQL generator.

Off unless ``CORTEX_VERIFIED_QUERY`` is set. When off, :func:`generation_examples`
returns ``None`` before touching the library.

Examples are question/SQL pairs. The generator (OpenVault FreeRoute, inside
the L2 port) sees them in its prompt and writes new SQL. This module does
not match phrasing, does not call a model, and does not execute stored SQL.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from CortexOS.memory.verified_query import get_verified_queries

ENABLED_ENV = "CORTEX_VERIFIED_QUERY"
ASK_ACTOR = "cortex:contract-ask"

_TRUTHY = {"1", "true", "on", "yes"}


def verified_query_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in _TRUTHY


def generation_examples(
    *,
    space_id: str,
    scored_pack_id: str | None = None,
) -> dict[str, Any] | None:
    """Prompt examples for one Space, or ``None`` when off or none are live.

    ``prompt`` is what the SQL generator may put in front of the question.
    ``verified_query_id`` names those examples. Memory fields name the C-MEM
    solutions that were read.
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
    prompt = [
        {"id": ex.query.id, "question": ex.query.question, "sql": ex.query.sql} for ex in found
    ]
    return {
        "prompt": prompt,
        "verified_query_id": ",".join(item["id"] for item in prompt),
        "memory_ids_read": [ex.entry.id for ex in found],
        "memory_reads": [
            {**ex.entry.provenance(), "served_at": ex.read_stamp.served_at} for ex in found
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
                space_id=bound.space_id, scored_pack_id=bound.scored_pack_id
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
) -> tuple[Token[Any], Token[Any], Any]:
    """Arm example loading for this ask. The L2 port is restored when the ask ends.

    A test that stubs ``resolve_l2_generation`` is left alone: its stub is not
    installed as the process port.
    """
    from CortexOS.dms import l2_generation

    restore: Any = None
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
    )


def take_verified_examples(
    tokens: tuple[Token[Any], Token[Any], Any] | None,
) -> dict[str, Any] | None:
    """Return examples that were placed in a prompt, and put the L2 port back."""
    meta = _USED.get()
    if tokens is not None:
        ask_token, used_token, restore = tokens
        _USED.reset(used_token)
        _ASK.reset(ask_token)
        if restore is not None:
            from CortexOS.dms.l2_generation import register_l2_generation

            register_l2_generation(restore)
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
