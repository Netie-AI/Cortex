"""CLARIFY (#307) ask seam for ``POST /v1/contract/ask``. Off unless ``CORTEX_CLARIFY=1``.

Off, :func:`try_clarify` returns None before reading anything, and the ask is
answered exactly as before. On, an ambiguous ask is answered with a stamped
clarify question instead of a guessed number, and an ask that re-sends the
chosen ``clarify_option_ids`` is answered as the option's resolved question.

Routing hints come from the answer engine's own deterministic router, so a
clarify fires only where that router would have guessed. No model is called.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from typing import Any

import yaml

from CortexOS.clarify.core import Catalog, Routed, Turn, catalog_from_semantic, render, resolve
from CortexOS.execution.manifest import VerifiedManifest

ENABLED_ENV = "CORTEX_CLARIFY"


def clarify_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def dms_catalog() -> Catalog:
    from CortexOS.dms.warehouse_db import ROOT, load_semantic_layer

    metrics_path = ROOT / "packs" / "dms" / "semantic" / "metrics.yaml"
    metrics = yaml.safe_load(metrics_path.read_text(encoding="utf-8")) or {}
    return catalog_from_semantic(metrics, load_semantic_layer() or {})


def dms_route(question: str) -> Routed:
    from CortexOS.dms.answer_engine import (
        _shape_refusal,
        match_certified,
        route_to_metric,
        undefined_subject,
    )
    from CortexOS.dms.query_service import route_question

    if (
        route_question(question) == "blocked"
        or _shape_refusal(question)
        or match_certified(question) is not None
        or undefined_subject(question)
    ):
        return Routed(settled=True)
    plan = route_to_metric(question)
    if plan is None:
        return Routed()
    return Routed(plan.metric_id, dict(plan.slots))


def try_clarify(
    question: str,
    option_ids: list[str],
    *,
    session_id: str,
    space_id: str | None,
    verified: VerifiedManifest,
) -> Turn | None:
    """The clarify turn for one contract ask, or None when clarify is off."""
    if not clarify_enabled():
        return None
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant

    try:
        resolve_product_grant(session_id, verified, space_id=space_id)
    except UngroundedSession:
        return None
    return resolve(question, option_ids, dms_catalog(), dms_route)


def clarify_answer(turn: Turn) -> dict[str, Any]:
    """The envelope for a clarify turn: the question, never a number."""
    clarify = turn.clarify
    if clarify is None:
        raise ValueError("turn carries no clarify question")
    return {
        "answer": render(clarify),
        "sql_used": None,
        "audit_id": str(uuid.uuid4()),
        "route": "clarify",
        "row_count": None,
        "rows": None,
        "provenance": {
            "layer": "clarify",
            "badge": "abstain",
            "assumptions": clarify.served_reason,
        },
        "clarify": clarify.model_dump(mode="json"),
        "clarify_resolved": list(turn.applied),
    }
