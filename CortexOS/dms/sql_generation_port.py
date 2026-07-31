"""L2 SQL-generation port — the engine side of the C7-full seam (C2 boundary).

``CortexOS`` may not import ``packs.*``. C7-full put schema retrieval, FreeRoute
generation and promotion in ``packs/dms/generative`` and had ``answer_engine``
import them directly, which broke the boundary contract. Rather than widen
``.importlinter``'s ignore list — which admits the debt instead of paying it —
the dependency is inverted the same way the audit ledger already does it
(``CortexOS/audit/ledger_registry.py``): the engine declares the port here, the
active vertical pack pushes an implementation in with :func:`register_sql_generation`,
and ``answer_engine`` pulls it back out. The arrow only ever points packs → CortexOS.

Deliberately narrow. The port exposes the four things the answer path actually
needs at L2 and nothing else, so a pack cannot smuggle behaviour into the engine
through a wide interface:

* ``is_configured``      — is a generation backend wired at all
* ``retrieve_schema``    — reduce the catalogue to what this question needs
* ``generate_candidates``— produce candidate SQL, given prior gate violations
* ``record_validated``   — record a promotion signal after the gate passes

What is **not** here on purpose: the validation gate itself
(``CortexOS/dms/sql_validate_gate.py``) and EXPLAIN. Those stay engine-side. A
pack supplies candidate SQL; only the engine decides whether it may run.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SqlGenerationProvider(Protocol):
    """What the engine needs from a pack to run L2 FreeRoute generation."""

    def is_configured(self) -> bool:
        """True when a generation backend is wired and permitted to be called."""
        ...

    def retrieve_schema(self, question: str) -> dict[str, Any]:
        """Reduced schema for this question — ``{"tables": {...}, ...}``."""
        ...

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str],
    ) -> list[str]:
        """Candidate SQL strings, best first. ``prior_violations`` drives retry."""
        ...

    def record_validated(self, question: str, sql: str) -> None:
        """Promotion signal after the engine's gate passed. Must never raise fatally."""
        ...


class SqlGenerationNotRegistered(RuntimeError):
    """No vertical pack registered an L2 generation provider for this install."""


_provider: SqlGenerationProvider | None = None


def register_sql_generation(provider: SqlGenerationProvider) -> None:
    """Install the active L2 provider. Called by the owning pack, never by the engine."""
    global _provider
    _provider = provider


def clear_sql_generation() -> None:
    """Drop the registered provider (pack swap / test teardown)."""
    global _provider
    _provider = None


def registered_sql_generation() -> SqlGenerationProvider | None:
    """The currently registered provider, without triggering a pack import."""
    return _provider


def resolve_sql_generation() -> SqlGenerationProvider:
    """Return the registered provider, importing the active pack once so it can register.

    Raises :class:`SqlGenerationNotRegistered` when the active pack ships no L2
    provider, so the answer path abstains cleanly rather than crashing. Abstain is
    the correct outcome there: no provider means no verified generation path.
    """
    if _provider is None:
        _load_active_pack()
    if _provider is None:
        raise SqlGenerationNotRegistered(
            "No L2 SQL generation provider is registered. The active vertical pack "
            "must call CortexOS.dms.sql_generation_port.register_sql_generation() "
            "(packs.dms does this on import)."
        )
    return _provider


def _load_active_pack() -> None:
    """Import the configured pack so its module-level registration runs.

    Resolved dynamically on purpose: a static ``import packs.…`` here would put the
    engine straight back on the wrong side of the C2 boundary.
    """
    from CortexOS.config import get_config

    try:
        pack = importlib.import_module(f"packs.{get_config().pack}")
    except ImportError:
        return  # pack absent or ships no L2 seam — resolve_sql_generation() reports it

    # An already-imported pack will not re-run its module-level registration, so a
    # resolve after clear_sql_generation() (pack swap, test teardown) would find
    # nothing and raise even though the pack is present. Re-arm it explicitly.
    if _provider is None:
        seams = getattr(pack, "register_engine_seams", None)
        if callable(seams):
            seams()


__all__ = [
    "SqlGenerationNotRegistered",
    "SqlGenerationProvider",
    "clear_sql_generation",
    "register_sql_generation",
    "registered_sql_generation",
    "resolve_sql_generation",
]
