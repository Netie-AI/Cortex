"""L2 SQL-generation port — engine owns the Protocol; the pack registers.

``CortexOS`` must not ``import packs.*`` (C2 / ``.importlinter``). The L2
path (schema retrieve → generate → promotion) lives in the vertical pack, so
the arrow is inverted the same way as ``CortexOS.audit.ledger_registry``.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol


class L2Generation(Protocol):
    def is_configured(self) -> bool: ...

    def retrieve(self, question: str) -> dict[str, Any]: ...

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any] | None = None,
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]: ...

    def record_validated(self, question: str, sql: str) -> Any: ...


_impl: L2Generation | None = None


def register_l2_generation(impl: L2Generation) -> None:
    """Install the active L2 generator. Called by the owning pack, never the engine."""
    global _impl
    _impl = impl


def clear_l2_generation() -> None:
    """Drop the registered generator (pack swap / test teardown)."""
    global _impl
    _impl = None


def resolve_l2_generation() -> L2Generation | None:
    """Return the registered L2 port, importing the active pack once so it can register."""
    if _impl is None:
        _load_active_pack()
    return _impl


def _load_active_pack() -> None:
    """Import the configured pack so its module-level registration runs.

    Dynamic on purpose: a static ``import packs.…`` here would re-break C2.
    Same shape as ``CortexOS.audit.ledger_registry``.
    """
    from CortexOS.config import get_config

    try:
        importlib.import_module(f"packs.{get_config().pack}")
    except ImportError:
        return
