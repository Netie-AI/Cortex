"""
CortexOS/api/memory_routes.py
Netie Memory dual-brain API (M0 store surface).

Backed by the M0 reference store for now (in-process brute-force KNN — exact
recall, the <10k tier). Real backends (rawknn-mmap, sqlite-vec, Qdrant,
pgvector) swap in behind the same VectorStore protocol as research-brief §D
findings land. Vectors are supplied by the caller until the embedder is wired
(brief E3).

No from __future__ import annotations (FastAPI rule).
Pydantic models at module level.
"""
from typing import Any

from fastapi import APIRouter, Depends
from netie.memory.factory import get_store
from netie.memory.store import (
    BRUTE_FORCE_MAX,
    Hit,
    MemoryRecord,
    select_store,
)
from pydantic import BaseModel

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/api/memory", tags=["memory"])

# Tests may replace this module-level store with an isolated backend instance.
_STORE = get_store()


class UpsertRecordIn(BaseModel):
    id: str
    text: str
    vector: list[float] | None = None
    meta: dict[str, Any] = {}
    scope: str = "personal"
    collection: str = "default"
    role: str | None = None
    tier: str = "warm"
    entry_scope: list[str] = []


class UpsertIn(BaseModel):
    records: list[UpsertRecordIn]


class QueryIn(BaseModel):
    vector: list[float]
    k: int = 5
    scope: str | None = None
    collection: str | None = None
    session_scope: list[str] | None = None


class AssembleIn(BaseModel):
    vector: list[float]
    k: int = 5
    scope: str | None = None
    session_scope: list[str] | None = None
    session_id: str | None = None


@router.post("/upsert")
async def memory_upsert(
    body: UpsertIn,
    caller: Caller = Depends(require_role("steward")),
) -> dict[str, Any]:
    _ = caller
    recs = []
    for r in body.records:
        entry = frozenset(t for t in (r.entry_scope or []) if t and str(t).strip())
        recs.append(
            MemoryRecord(
                id=r.id, text=r.text, vector=r.vector, meta=r.meta,
                scope=r.scope if r.scope in ("personal", "company") else "personal",
                collection=r.collection, role=r.role,
                tier=r.tier if r.tier in ("hot", "warm", "cold") else "warm",
                entry_scope=entry,
            )
        )
    n = _STORE.upsert(recs)
    stats = _STORE.stats()
    return {"ok": True, "upserted": n, "stats": stats,
            "recommended_store": select_store(int(stats.get("count") or 0))}


@router.post("/query")
async def memory_query(
    body: QueryIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    sess = (
        frozenset(t for t in body.session_scope if t and str(t).strip())
        if body.session_scope is not None
        else None
    )
    hits: list[Hit] = _STORE.query(
        body.vector, k=body.k,
        scope=body.scope if body.scope in ("personal", "company") else None,
        collection=body.collection,
        session_scope=sess,
    )
    return {"ok": True, "hits": [
        {"id": h.id, "score": round(h.score, 6), "text": h.text, "meta": h.meta}
        for h in hits
    ]}


@router.post("/assemble")
async def memory_assemble(
    body: AssembleIn,
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    """Assemble vector + optional chat/note layers into one prompt-ready blob."""
    _ = caller
    from netie.memory.context_provider import MemoryContextProvider

    sess = (
        frozenset(t for t in body.session_scope if t and str(t).strip())
        if body.session_scope is not None
        else None
    )
    scope = body.scope if body.scope in ("personal", "company") else None
    assembled = MemoryContextProvider(_STORE).assemble(
        body.vector,
        k=body.k,
        scope=scope,
        session_scope=sess,
        session_id=body.session_id,
    )
    vector_hits = assembled.get("vector_hits") or []
    return {
        "ok": True,
        "text_blob": assembled.get("text_blob") or "",
        "hits": [
            {"id": h.id, "score": round(h.score, 6), "text": h.text, "meta": h.meta}
            for h in vector_hits
        ],
        "layers": {
            "vector_count": len(vector_hits),
            "chat_count": len(assembled.get("chat_turns") or []),
            "notes_count": len(assembled.get("notes") or []),
        },
    }


@router.get("/stats")
async def memory_stats(caller: Caller = Depends(require_role("viewer"))) -> dict[str, Any]:
    _ = caller
    stats = _STORE.stats()
    return {"ok": True, **stats,
            "brute_force_max": BRUTE_FORCE_MAX,
            "recommended_store": select_store(int(stats.get("count") or 0))}


def register_memory_routes(app: Any) -> None:
    app.include_router(router)
