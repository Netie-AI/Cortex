"""DMS implementation of the engine's L2 SQL-generation port (C2 inversion).

The engine declares the seam in ``CortexOS/dms/sql_generation_port.py`` and never
imports this module; ``packs/dms/__init__.py`` pushes the singleton below in at
pack load. Same shape as the audit-ledger registration next to it.

Every heavy import is inside a method body on purpose. Registration happens on
``import packs.dms``, which is on the engine's startup path — pulling the FreeRoute
client and the embedding cache in at that moment would make every process that
merely loads the pack pay for a feature that is off by default (``DMS_L2_ENABLED``).

This adapter is a thin translation layer only. It normalises signatures to the
port and swallows nothing that matters: a generation failure returns an empty
candidate list so the engine abstains, which is the correct outcome — the one
exception is ``record_validated``, a telemetry signal that must never be able to
fail an answer that already passed the gate.
"""

from __future__ import annotations

from typing import Any


class DmsSqlGeneration:
    """C7-full: schema retrieval → FreeRoute generation → promotion signal."""

    def is_configured(self) -> bool:
        from packs.dms.generative import sql_generator

        return bool(sql_generator.is_configured())

    def retrieve_schema(self, question: str) -> dict[str, Any]:
        from packs.dms.generative import schema_retrieval

        return schema_retrieval.retrieve(question)

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str],
    ) -> list[str]:
        from packs.dms.generative import sql_generator

        return sql_generator.generate_candidates(
            question,
            schema,
            prior_violations=prior_violations,
        )

    def record_validated(self, question: str, sql: str) -> None:
        from packs.dms.generative import promotion

        # A promotion signal must never sink an answer that already passed the gate.
        try:
            promotion.record_validated(question, sql)
        except Exception:  # noqa: BLE001
            pass


provider = DmsSqlGeneration()

__all__ = ["DmsSqlGeneration", "provider"]
