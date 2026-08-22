"""L2 generation port — engine owns the seam, the pack fills it (C2).

``CortexOS`` must not import ``packs.*``. Schema retrieval, SQL generation and
L2 promotion live in the vertical pack, so the arrow is inverted: the engine
declares this port, the active pack registers an implementation, and
``answer_engine`` only ever holds the port.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol


class L2GenerationPort(Protocol):
    def is_configured(self) -> bool: ...

    def retrieve_schema(self, question: str) -> dict[str, Any]: ...

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]: ...

    def record_validated(self, question: str, sql: str) -> Any: ...


class L2NotRegistered(RuntimeError):
    """No vertical pack registered an L2 generation implementation."""


_port: L2GenerationPort | None = None


def register_l2_generation(port: L2GenerationPort) -> None:
    """Install the active L2 port. Called by the owning pack, never by the engine."""
    global _port
    _port = port


def clear_l2_generation() -> None:
    """Drop the registered port (pack swap / test teardown)."""
    global _port
    _port = None


def resolve_l2_generation() -> L2GenerationPort:
    """Return the registered L2 port, importing the active pack once so it can register."""
    if _port is None:
        _load_active_pack()
    if _port is None:
        raise L2NotRegistered(
            "No L2 generation port is registered. The active vertical pack must "
            "call CortexOS.dms.l2_generation.register_l2_generation()."
        )
    return _port


def _load_active_pack() -> None:
    """Import the configured pack so its module-level registration runs.

    Resolved dynamically on purpose: a static ``import packs.…`` here would put
    the engine straight back on the wrong side of the C2 boundary.
    """
    from CortexOS.config import get_config

    try:
        importlib.import_module(f"packs.{get_config().pack}")
    except ImportError:
        return


__all__ = [
    "L2GenerationPort",
    "L2NotRegistered",
    "clear_l2_generation",
    "register_l2_generation",
    "resolve_l2_generation",
]
