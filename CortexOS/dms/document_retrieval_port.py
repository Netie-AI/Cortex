"""RAG document-retrieval port — engine side of the space-scoped chunk seam (C2).

``CortexOS`` may not import ``packs.*``. The active vertical pack registers an
implementation via :func:`register_document_retrieval`; the answer path pulls it
back through :func:`resolve_document_retrieval`. Scope filtering (``space_id``,
optional ``source_ids``) must happen in the pack's storage query — never as a
Python post-filter over an unscoped corpus.

Deliberately narrow: configured check + retrieve only. Fusion/rerank stay
engine-side or in ``CortexOS/rag/*``; the pack supplies ranked chunk rows.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DocumentRetrievalProvider(Protocol):
    """What the engine needs from a pack to run space-scoped document retrieval."""

    def is_configured(self) -> bool:
        """True when a retrieval backend is wired and permitted to be called."""
        ...

    def retrieve(
        self,
        question: str,
        *,
        space_id: str,
        top_k: int = 8,
        source_ids: list[str] | None = None,
        depth: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ranked chunk hits — each row carries ``space_id`` and ``source_id``."""
        ...


class DocumentRetrievalNotRegistered(RuntimeError):
    """No vertical pack registered a document retrieval provider for this install."""


_provider: DocumentRetrievalProvider | None = None


def register_document_retrieval(provider: DocumentRetrievalProvider) -> None:
    """Install the active retrieval provider. Called by the owning pack, never by the engine."""
    global _provider
    _provider = provider


def clear_document_retrieval() -> None:
    """Drop the registered provider (pack swap / test teardown)."""
    global _provider
    _provider = None


def registered_document_retrieval() -> DocumentRetrievalProvider | None:
    """The currently registered provider, without triggering a pack import."""
    return _provider


def resolve_document_retrieval() -> DocumentRetrievalProvider:
    """Return the registered provider, importing the active pack once so it can register.

    Raises :class:`DocumentRetrievalNotRegistered` when the active pack ships no
    retrieval provider, so the answer path abstains cleanly rather than crashing.
    """
    if _provider is None:
        _load_active_pack()
    if _provider is None:
        raise DocumentRetrievalNotRegistered(
            "No document retrieval provider is registered. The active vertical pack "
            "must call CortexOS.dms.document_retrieval_port.register_document_retrieval() "
            "(packs.dms does this on import)."
        )
    return _provider


def _load_active_pack() -> None:
    """Import the configured pack so its module-level registration runs."""
    from CortexOS.config import get_config

    try:
        pack = importlib.import_module(f"packs.{get_config().pack}")
    except ImportError:
        return

    if _provider is None:
        seams = getattr(pack, "register_engine_seams", None)
        if callable(seams):
            seams()


__all__ = [
    "DocumentRetrievalNotRegistered",
    "DocumentRetrievalProvider",
    "clear_document_retrieval",
    "register_document_retrieval",
    "registered_document_retrieval",
    "resolve_document_retrieval",
]
