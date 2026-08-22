"""L2 generation port — the engine side of the C2 boundary.

``CortexOS`` may not import ``packs.*``. L2 schema retrieval, SQL generation,
and promotion live in the DMS pack, so the dependency is inverted: this module
owns the port, ``packs.dms.register_engine_seams`` pushes the implementations
in, and ``answer_engine`` pulls them back out with :func:`resolve_l2`.

A provider is the three pack modules (attribute lookup at call time), so
``unittest.mock.patch`` / ``monkeypatch.setattr`` on those modules still lands.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any


class L2NotRegistered(RuntimeError):
    """No vertical pack registered an L2 generator for this install."""


@dataclass(frozen=True, slots=True)
class L2Port:
    sql_generator: Any
    schema_retrieval: Any
    promotion: Any


_port: L2Port | None = None


def register_l2(
    sql_generator: Any,
    schema_retrieval: Any,
    promotion: Any,
) -> None:
    """Install the L2 modules. Called by the owning pack, never by the engine."""
    global _port
    _port = L2Port(
        sql_generator=sql_generator,
        schema_retrieval=schema_retrieval,
        promotion=promotion,
    )


def clear_l2() -> None:
    """Drop the registered L2 port (pack swap / test teardown)."""
    global _port
    _port = None


def registered_l2() -> L2Port | None:
    """The currently registered port, without triggering a pack import."""
    return _port


def resolve_l2() -> L2Port:
    """Return the registered L2 port, importing the DMS pack once so it can register.

    L2 is a DMS vertical (``DMS_L2_ENABLED`` / ``packs.dms.generative``). The
    previous answer-engine path hardcoded that pack; this keeps the same target
    so ``PACK=ruma`` in tests still reaches the DMS generator when L2 is on.

    Raises :class:`L2NotRegistered` when the pack ships no L2 seam.
    """
    if _port is None:
        _load_dms_pack()
    if _port is None:
        raise L2NotRegistered(
            "No L2 generator is registered. packs.dms must call "
            "CortexOS.dms.l2_registry.register_l2() on import."
        )
    return _port


def _load_dms_pack() -> None:
    """Import packs.dms so its module-level registration runs.

    Dynamic on purpose: a static ``import packs.…`` here would put the engine
    back on the wrong side of C2. Same shape as ``CortexOS.audit.ledger_registry``.
    """
    try:
        importlib.import_module("packs.dms")
    except ImportError:
        return


__all__ = [
    "L2NotRegistered",
    "L2Port",
    "clear_l2",
    "register_l2",
    "registered_l2",
    "resolve_l2",
]
