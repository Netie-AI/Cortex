"""SCHEMA-RETRIEVE (#306) ask seam: stamp what was retrieved before planning.

Off unless ``CORTEX_SCHEMA_RETRIEVE=1``; off, it returns ``{}`` without loading
a catalog, so the contract answer is byte-identical. On, the grant and the
Space come from the *signed* session grant, never from a caller string. With
per-Space memory on (``CORTEX_SPACE_MEMORY=1``) the Space's table and formula
memory is read, and chosen catalog tables are learned into table memory through
the #290 store (which refuses scored-pack writes). Deterministic only: no model
is called on this path.
"""

from __future__ import annotations

import os
from typing import Any

from CortexOS.execution.manifest import VerifiedManifest

ENABLED_ENV = "CORTEX_SCHEMA_RETRIEVE"
ASK_ACTOR = "cortex:contract-ask:schema-retrieve"


def schema_retrieve_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in {"1", "true", "on", "yes"}


def schema_retrieval_fields(
    question: str,
    *,
    session_id: str,
    space_id: str | None,
    verified: VerifiedManifest,
    scored_pack_id: str | None = None,
) -> dict[str, Any]:
    if not schema_retrieve_enabled():
        return {}
    from CortexOS.dms.answer_engine import UngroundedSession, resolve_product_grant
    from CortexOS.memory.space_memory import get_space_memory, space_memory_enabled
    from CortexOS.ontology.registry import pack_dir_for
    from CortexOS.schema_retrieve import DeterministicSchemaRetriever, GrantScope
    from CortexOS.schema_retrieve.catalog import load_pack_catalog

    try:
        grant, _, _ = resolve_product_grant(session_id, verified, space_id=space_id)
    except UngroundedSession:
        return {}
    memory = get_space_memory() if space_memory_enabled() else None
    retriever = DeterministicSchemaRetriever(
        lambda: load_pack_catalog(pack_dir_for()),
        memory=memory,
        remember=memory is not None,
        actor=ASK_ACTOR,
    )
    result = retriever.retrieve(
        question, grant=GrantScope.from_manifest(grant.manifest), scored_pack_id=scored_pack_id
    )
    return {"schema_retrieval": result.stamp.model_dump()}
