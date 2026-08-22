"""DMS implementation of the engine L2 generation port."""

from __future__ import annotations

from typing import Any


class DmsL2Generation:
    """Thin adapter so CortexOS.dms.answer_engine never imports this package."""

    def is_configured(self) -> bool:
        from packs.dms.generative import sql_generator

        return sql_generator.is_configured()

    def retrieve_schema(self, question: str) -> dict[str, Any]:
        from packs.dms.generative import schema_retrieval

        return schema_retrieval.retrieve(question)

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]:
        from packs.dms.generative import sql_generator

        return sql_generator.generate_candidates(
            question, schema, prior_violations=prior_violations
        )

    def record_validated(self, question: str, sql: str) -> Any:
        from packs.dms.generative import promotion

        return promotion.record_validated(question, sql)
