"""DMS implementation of the engine L2 generation port."""

from __future__ import annotations

from typing import Any


class DmsL2Generation:
    """Thin adapter so CortexOS.dms.answer_engine never imports this package."""

    def __init__(self) -> None:
        # Metadata of the last generate_candidates call, for shadow/A-B comparison.
        self.last_detail: dict[str, Any] = {"few_shot_count": 0, "few_shot_enabled": True}

    @property
    def few_shot_count(self) -> int:
        """Verified examples placed in the last L2 prompt (0 when DMS_L2_FEW_SHOT=0)."""
        return int(self.last_detail.get("few_shot_count") or 0)

    def detail(self) -> dict[str, Any]:
        """Shadow metadata for the last generation. A copy; never raises."""
        return dict(self.last_detail)

    def is_configured(self) -> bool:
        from packs.dms.generative import sql_generator

        return sql_generator.is_configured()

    def unarmed_reason(self) -> str:
        """Why generation is not wired (flag off, FreeRoute not armed), for the abstain."""
        from packs.dms.generative import sql_generator

        return sql_generator.unarmed_reason()

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

        try:
            return sql_generator.generate_candidates(
                question, schema, prior_violations=prior_violations
            )
        finally:
            self.last_detail = {
                "few_shot_count": sql_generator.last_few_shot_count(),
                "few_shot_enabled": sql_generator.few_shot_enabled(),
            }

    def record_validated(self, question: str, sql: str) -> Any:
        from packs.dms.generative import promotion

        return promotion.record_validated(question, sql)

    def leftover_literals(self, sql: str) -> list[str]:
        from packs.dms.generative.literal_normalize import normalize_sql_literals

        result = normalize_sql_literals(sql)
        if not result.ok:
            return list(result.violations)
        return [f"{col}:{old}->{new}" for col, old, new in result.replacements]
