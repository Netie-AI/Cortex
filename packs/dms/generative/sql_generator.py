"""C7 L2 SQL generator stub — abstain until FreeRoute/model is configured.

Full C7 wires schema retrieval -> generate_candidates -> sql_validate_gate.
Do not call legacy query_service.generate_sql heuristics as L2.
"""

from __future__ import annotations


def is_configured() -> bool:
    """True only when a real NL->SQL model endpoint is wired."""
    return False


def generate_candidates(
    question: str,
    schema_context: dict | None = None,
    *,
    n: int = 3,
    prior_violations: list[str] | None = None,
) -> list[str]:
    """Return SQL candidates. Empty until is_configured() is True."""
    _ = (question, schema_context, n, prior_violations)
    if not is_configured():
        return []
    return []


__all__ = ["generate_candidates", "is_configured"]
