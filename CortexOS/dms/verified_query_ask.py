"""VERIFIED-QUERY (#309): load steward-confirmed examples for the SQL generator.

Off unless ``CORTEX_VERIFIED_QUERY`` is set. When off, :func:`generation_examples`
returns ``None`` before touching the library.

Which examples belong with the question is one OpenVault FreeRoute pick
(``vq-pick-examples``). That pick is not a word-overlap rank. The SQL
generator then writes new SQL. This module does not execute stored SQL.
"""

from __future__ import annotations

import json
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from CortexOS.memory.verified_query import get_verified_queries

ENABLED_ENV = "CORTEX_VERIFIED_QUERY"
ASK_ACTOR = "cortex:contract-ask"
PICK_TASK = "vq-pick-examples"
# Token budget for the pick call. Id order, not similarity.
CANDIDATE_BUDGET = 32

_TRUTHY = {"1", "true", "on", "yes"}


def verified_query_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in _TRUTHY


def _parse_ids(text: str, allowed: set[str]) -> list[str]:
    """Ids the model named that are in ``allowed``, in the model's order."""
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        qid = str(item).strip()
        if qid in allowed and qid not in seen:
            seen.add(qid)
            out.append(qid)
    return out


def pick_example_ids(question: str, candidates: list[tuple[str, str]]) -> list[str]:
    """One FreeRoute pick. Empty on refusal. Never a shared-word ranking.

    ``candidates`` is ``(id, question)``. At most :data:`CANDIDATE_BUDGET` are
    shown, in id order, which is a token budget. The model chooses which of
    those ids go into the SQL prompt, at most :data:`EXAMPLE_CAP`.
    """
    from CortexOS.integrations import freeroute
    from CortexOS.memory.verified_query import EXAMPLE_CAP

    by_id: dict[str, str] = {}
    for qid, text in candidates:
        key = str(qid).strip()
        if key and key not in by_id:
            by_id[key] = str(text or "")
    if not by_id:
        return []
    shown = sorted(by_id)[:CANDIDATE_BUDGET]
    lines = [f"{qid}\t{by_id[qid]}" for qid in shown]
    prompt = (
        "Choose verified-query ids for QUESTION. "
        "Reply with a JSON array of ids and nothing else.\n"
        "CANDIDATES:\n" + "\n".join(lines) + f"\nQUESTION:\n{question}"
    )
    out = freeroute.complete(
        PICK_TASK,
        [{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200,
        timeout=30.0,
        egress="leave",
    )
    if not getattr(out, "ok", False) or not getattr(out, "text", None):
        return []
    return _parse_ids(str(out.text), set(shown))[:EXAMPLE_CAP]


def generation_examples(
    *,
    space_id: str,
    question: str,
    scored_pack_id: str | None = None,
) -> dict[str, Any] | None:
    """Prompt examples for one Space, or ``None`` when off, empty, or unpicked.

    The model names the ids. A refusal does not fall back to shared words.
    ``verified_query_id`` names the picked examples. Memory fields name those
    C-MEM solutions.
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
    chosen = pick_example_ids(
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
