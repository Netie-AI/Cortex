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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from netie.memory.store import (
    BRUTE_FORCE_MAX,
    Hit,
    InMemoryStore,
    MemoryRecord,
    select_store,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])

# M0 singleton — swapped for a store factory (select_store) once persistent
# backends exist. Personal scope only until role labels land (M4).
_STORE = InMemoryStore()


class UpsertRecordIn(BaseModel):
    id: str
    text: str
    vector: Optional[List[float]] = None
    meta: Dict[str, Any] = {}
    scope: str = "personal"
    collection: str = "default"
    role: Optional[str] = None
    tier: str = "warm"


class UpsertIn(BaseModel):
    records: List[UpsertRecordIn]


class QueryIn(BaseModel):
    vector: List[float]
    k: int = 5
    scope: Optional[str] = None
    collection: Optional[str] = None


@router.post("/upsert")
async def memory_upsert(body: UpsertIn) -> Dict[str, Any]:
    recs = [
        MemoryRecord(
            id=r.id, text=r.text, vector=r.vector, meta=r.meta,
            scope=r.scope if r.scope in ("personal", "company") else "personal",
            collection=r.collection, role=r.role,
            tier=r.tier if r.tier in ("hot", "warm", "cold") else "warm",
        )
        for r in body.records
    ]
    n = _STORE.upsert(recs)
    stats = _STORE.stats()
    return {"ok": True, "upserted": n, "stats": stats,
            "recommended_store": select_store(int(stats.get("count") or 0))}


@router.post("/query")
async def memory_query(body: QueryIn) -> Dict[str, Any]:
    hits: List[Hit] = _STORE.query(
        body.vector, k=body.k,
        scope=body.scope if body.scope in ("personal", "company") else None,
        collection=body.collection,
    )
    return {"ok": True, "hits": [
        {"id": h.id, "score": round(h.score, 6), "text": h.text, "meta": h.meta}
        for h in hits
    ]}


@router.get("/stats")
async def memory_stats() -> Dict[str, Any]:
    stats = _STORE.stats()
    return {"ok": True, **stats,
            "brute_force_max": BRUTE_FORCE_MAX,
            "recommended_store": select_store(int(stats.get("count") or 0))}


def register_memory_routes(app: Any) -> None:
    app.include_router(router)
