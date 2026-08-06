"""Semantic-layer port — the engine side of the metric/certified-query seam (C2).

``CortexOS/**`` must never import ``packs.*``. The answer engine was the loudest
violation of that: it reached directly into ``packs.dms.semantic`` for the
loader, the value dictionary, the routing vocabulary, the query-skill store and
the catalog builder — 13 crossings across 5 modules, on the hottest path in the
repo.

They were invisible for a long time because ``packs/dms/semantic/`` had no
``__init__.py``, so grimp could not see the package and ``lint-imports``
reported the boundary green while blind to it (C2-01). The directory is a
package now, which is what makes this port necessary rather than optional.

The shape is the one already proven here by ``ledger_registry`` and
``sql_generation_port``: the engine declares what it needs, the active vertical
pack registers an implementation at load, and nothing in ``CortexOS`` imports a
pack. What the engine gets back is deliberately plain — dicts, strings, and one
port-owned exception — so a second pack could satisfy this without inheriting
DMS's types.
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol, runtime_checkable


class SemanticLayerNotRegistered(RuntimeError):
    """No vertical pack registered a semantic layer for this install."""


class SemanticCompileError(ValueError):
    """A metric could not be compiled — unknown id, bad params, failed guardrail.

    Owned by the engine, not the pack. The engine has to branch on this (an
    exclusion that fails to resolve must abstain rather than fall through to a
    query-skill replay), and branching on a pack's exception class would be the
    import this port exists to remove.
    """


@runtime_checkable
class SemanticLayerProvider(Protocol):
    """What the answer engine needs from a pack's semantic layer.

    Derived from the call sites, not from what the pack happens to export — a
    port wider than its use is a boundary that drifts back into a dependency.
    """

    def load_model(self) -> Any:
        """The semantic model: metrics, certified queries, and base tables."""
        ...

    def compile_metric(
        self, metric_id: str, params: dict[str, Any], *, resolve: bool = True
    ) -> str:
        """Render a governed metric to validated SQL.

        Raises :class:`SemanticCompileError` — never the pack's own type.
        """
        ...

    def normalize_for_routing(self, question: str) -> str:
        """Business phrasing → router vocabulary. Must never move a slot value."""
        ...

    def resolve_value(self, entity_text: str, column: str) -> Any:
        """Resolve NL text to the column's real encoding (``BETA`` → ``SKU-BETA``)."""
        ...

    def metric_label(self, metric: Any) -> str:
        """Human label for a metric — its first synonym, or its id."""
        ...

    def is_catalog_intent(self, question: str) -> bool:
        """True when the user is asking what they can query, not asking for data."""
        ...

    def build_catalog_answer(self) -> dict[str, str]:
        """Prose catalog of certified questions, metrics and tables. No SQL."""
        ...

    def find_skill(self, question: str) -> dict[str, Any] | None:
        """A previously captured question→SQL skill, if one matches."""
        ...

    def capture_skill(
        self,
        question: str,
        *,
        metric_id: str | None,
        params: dict[str, Any] | None,
        sql: str | None,
        layer: str,
    ) -> None:
        """Graduate a successful answer into the skill store. Never raises."""
        ...


_provider: SemanticLayerProvider | None = None


def register_semantic_layer(provider: SemanticLayerProvider) -> None:
    """Install the active semantic layer. Called by the owning pack, never the engine."""
    global _provider
    _provider = provider


def clear_semantic_layer() -> None:
    """Drop the registered provider (pack swap / test teardown)."""
    global _provider
    _provider = None


def registered_semantic_layer() -> SemanticLayerProvider | None:
    """The currently registered provider, without triggering a pack import."""
    return _provider


def resolve_semantic_layer() -> SemanticLayerProvider:
    """Return the registered provider, importing the active pack once so it can register."""
    if _provider is None:
        _load_active_pack()
    if _provider is None:
        raise SemanticLayerNotRegistered(
            "No semantic layer is registered. The active vertical pack must call "
            "CortexOS.dms.semantic_port.register_semantic_layer() (packs.dms does "
            "this on import)."
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
    "SemanticCompileError",
    "SemanticLayerNotRegistered",
    "SemanticLayerProvider",
    "clear_semantic_layer",
    "register_semantic_layer",
    "registered_semantic_layer",
    "resolve_semantic_layer",
]
