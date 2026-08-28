"""Assembly of vector, chat, and note context for a memory-aware prompt."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from netie.memory.store import Scope, VectorStore

ContextLoader = Callable[..., list[object]]


class MemoryContextProvider:
    """Collect relevant persistent memories and optional conversational context."""

    def __init__(
        self,
        store: VectorStore,
        notes_loader: ContextLoader | None = None,
        chat_loader: ContextLoader | None = None,
    ) -> None:
        self.store = store
        self.notes_loader = notes_loader
        self.chat_loader = chat_loader

    def assemble(
        self,
        query_vector: list[float],
        *,
        k: int = 5,
        scope: Scope | None = None,
        session_scope: frozenset[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        """Return memory hits and optional text context for a query.

        When ``session_scope`` is set, the store applies ``entry_scope ⊆ session_scope``
        in its storage query (C6) — not after ranking.
        """
        vector_hits = self.store.query(
            query_vector, k=k, scope=scope, session_scope=session_scope
        )
        chat_turns = self._load(
            self.chat_loader,
            query_vector=query_vector,
            k=k,
            scope=scope,
            session_scope=session_scope,
            session_id=session_id,
        )
        notes = self._load(
            self.notes_loader,
            query_vector=query_vector,
            k=k,
            scope=scope,
            session_scope=session_scope,
            session_id=session_id,
        )
        text_blob = "\n".join(
            text
            for text in (
                *(hit.text for hit in vector_hits),
                *(self._text(item) for item in chat_turns),
                *(self._text(item) for item in notes),
            )
            if text
        )
        return {
            "vector_hits": vector_hits,
            "chat_turns": chat_turns,
            "notes": notes,
            "text_blob": text_blob,
            "layers": {
                "session": chat_turns,
                "vector": vector_hits,
                "long_term": notes,
            },
        }

    @staticmethod
    def _load(loader: ContextLoader | None, **kwargs: object) -> list[object]:
        if loader is None:
            return []
        signature = inspect.signature(loader)
        accepted = {
            name: value
            for name, value in kwargs.items()
            if name in signature.parameters
            or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        }
        result = loader(**accepted)
        return list(result) if result is not None else []

    @staticmethod
    def _text(item: object) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            for key in ("text", "content", "message"):
                value = item.get(key)
                if isinstance(value, str):
                    return value
        return str(item)
