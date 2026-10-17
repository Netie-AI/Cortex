"""Per-request ask scope: the signed Space and scored pack of the ask in flight.

``POST /v1/contract/ask`` opens this around the engine call, so a store the
engine writes to on the way (the legacy ``query_skill`` store, #340) can refuse
a scored-pack write or scope a read to the Space the signed grant names. Outside
any scope there is no Space and no pack: a scoped store reads and writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
