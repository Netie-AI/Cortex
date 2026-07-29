from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class LedgerEntry(BaseModel):
    id: str
    seq: int
    actor: str
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str
    created_at: str


class ChainVerification(BaseModel):
    ok: bool
    broken_at: int | None = None


class LedgerWriter(Protocol):
    def append(
        self,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        db_path: str | None = None,
    ) -> Any: ...

    def verify(
        self, *, db_path: str | None = None, start_seq: int = 0
    ) -> Any: ...

    def list_entries(
        self,
        *,
        db_path: str | None = None,
        from_seq: int = 0,
        limit: int = 100,
        event_type: str | None = None,
    ) -> Any: ...
