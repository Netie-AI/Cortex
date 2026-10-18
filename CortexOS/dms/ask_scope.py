"""Per-request ask scope: the signed Space and scored pack of the ask in flight.

``POST /v1/contract/ask`` opens this around the engine call, so a store the
engine writes to on the way (the legacy ``query_skill`` store, #340) can refuse
a scored-pack write or scope a read to the Space the signed grant names. Outside
any scope there is no Space and no pack: a scoped store reads and writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AskScope:
    space_id: str | None = None
    scored_pack_id: str | None = None


_NO_SCOPE = AskScope()
_SCOPE: ContextVar[AskScope] = ContextVar("cortex_ask_scope", default=_NO_SCOPE)


def current_ask_scope() -> AskScope:
    return _SCOPE.get()


@contextmanager
def ask_scope(*, space_id: str | None, scored_pack_id: str | None) -> Iterator[AskScope]:
    scope = AskScope(
        space_id=(space_id or "").strip() or None,
        scored_pack_id=(scored_pack_id or "").strip() or None,
    )
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


# VERIFIED-QUERY (#309): true while the ask in flight has steward-confirmed
# examples bound for the SQL generator (``verified_query_ask``). The query-skill
# store reads it to stand aside for that ask only; every other ask keeps its
# skills. Outside such an ask it is false.
_EXAMPLES_BOUND: ContextVar[bool] = ContextVar("cortex_ask_examples_bound", default=False)


def examples_bound() -> bool:
    return _EXAMPLES_BOUND.get()


def mark_examples_bound() -> Token[bool]:
    return _EXAMPLES_BOUND.set(True)


def clear_examples_bound(token: Token[bool]) -> None:
    _EXAMPLES_BOUND.reset(token)
