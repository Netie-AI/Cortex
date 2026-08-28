"""Ledger provider registry — the engine side of the audit seam (C2 boundary).

``CortexOS`` may not import ``packs.*`` (``tests/contract/test_import_boundaries.py``).
The hash-chained audit ledger is classed *engine governance* in
``docs/strategy/DMS_C2_CLASSIFICATION_2026-07-29.md``, but the implementation
still lives in ``packs/dms/audit`` until the C2 file move lands. So the
dependency is inverted rather than allowlisted: the engine owns the port
declared here, the active vertical pack pushes its implementation in with
:func:`register_ledger`, and engine code pulls it back out with
:func:`resolve_ledger`. The arrow only ever points packs → CortexOS.

A provider is anything satisfying ``cortex_contract.ledger.LedgerWriter``
(``append`` / ``verify`` / ``list_entries``) — a module object qualifies, and
keeping it a module means ``unittest.mock.patch`` on the pack's functions still
lands, because attributes resolve at call time.
"""

from __future__ import annotations

import importlib

from cortex_contract.ledger import LedgerWriter


class LedgerNotRegistered(RuntimeError):
    """No vertical pack registered an audit ledger for this install."""


_ledger: LedgerWriter | None = None


def register_ledger(writer: LedgerWriter) -> None:
    """Install the active audit ledger. Called by the owning pack, never by the engine."""
    global _ledger
    _ledger = writer


def clear_ledger() -> None:
    """Drop the registered ledger (pack swap / test teardown)."""
    global _ledger
    _ledger = None


def registered_ledger() -> LedgerWriter | None:
    """The currently registered ledger, without triggering a pack import."""
    return _ledger


def resolve_ledger() -> LedgerWriter:
    """Return the registered ledger, importing the active pack once so it can register.

    Raises :class:`LedgerNotRegistered` when the active pack ships no ledger, so
    callers surface a clean 501 instead of crashing.
    """
    if _ledger is None:
        _load_active_pack()
    if _ledger is None:
        raise LedgerNotRegistered(
            "No audit ledger is registered. The active vertical pack must call "
            "CortexOS.audit.register_ledger() with an append/verify/list_entries "
            "provider (packs.dms does this on import)."
        )
    return _ledger


def _load_active_pack() -> None:
    """Import the configured pack so its module-level registration runs.

    Resolved dynamically on purpose: a static ``import packs.…`` here would put
    the engine straight back on the wrong side of the C2 boundary. Same shape as
    ``CortexOS/a2a/personas``.
    """
    from CortexOS.config import get_config

    try:
        importlib.import_module(f"packs.{get_config().pack}")
    except ImportError:
        return  # pack absent or ships no seams — resolve_ledger() reports it


__all__ = [
    "LedgerNotRegistered",
    "LedgerWriter",
    "clear_ledger",
    "register_ledger",
    "registered_ledger",
    "resolve_ledger",
]
