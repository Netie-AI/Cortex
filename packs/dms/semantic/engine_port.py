"""DMS implementation of the engine's semantic-layer port (C2 inversion).

The engine declares the seam in ``CortexOS/dms/semantic_port.py`` and never
imports this module; ``packs/dms/__init__.py`` pushes the singleton below in at
pack load.

This is an adapter, not a re-export. It translates the pack's own
``SemanticError`` into the port's ``SemanticCompileError`` so the engine can
branch on a failed compile without importing a pack exception — which is the
crossing the port exists to remove.
"""

from __future__ import annotations

from typing import Any

from CortexOS.dms.semantic_port import SemanticCompileError


class DmsSemanticLayer:
    """The DMS metric / certified-query / value-dictionary layer."""

    def load_model(self) -> Any:
        from packs.dms.semantic.loader import load_all

        return load_all()

    def compile_metric(
        self, metric_id: str, params: dict[str, Any], *, resolve: bool = True
    ) -> str:
        from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

        try:
            return compile_metric(load_all(), metric_id, params, resolve=resolve)
        except SemanticError as exc:
            # Re-raised as the port's type, with the cause kept so a failed
            # guardrail is still diagnosable from the traceback.
            raise SemanticCompileError(str(exc)) from exc

    def normalize_for_routing(self, question: str) -> str:
        from packs.dms.semantic.vocabulary import normalize_for_routing

        return normalize_for_routing(question)

    def resolve_value(self, entity_text: str, column: str) -> Any:
        from packs.dms.semantic import values

        return values.resolve(entity_text, column)

    def metric_label(self, metric: Any) -> str:
        from packs.dms.semantic.catalog_answer import _metric_label

        return _metric_label(metric)

    def is_catalog_intent(self, question: str) -> bool:
        from packs.dms.semantic.catalog_answer import is_catalog_intent

        return is_catalog_intent(question)

    def build_catalog_answer(self) -> dict[str, str]:
        from packs.dms.semantic.catalog_answer import build_catalog_answer

        return build_catalog_answer()

    def find_skill(self, question: str) -> dict[str, Any] | None:
        from packs.dms.semantic import query_skills

        return query_skills.find(question)

    def capture_skill(
        self,
        question: str,
        *,
        metric_id: str | None,
        params: dict[str, Any] | None,
        sql: str | None,
        layer: str,
    ) -> None:
        from packs.dms.semantic import query_skills

        query_skills.capture(
            question, metric_id=metric_id, params=params, sql=sql, layer=layer
        )


provider = DmsSemanticLayer()

__all__ = ["DmsSemanticLayer", "provider"]
