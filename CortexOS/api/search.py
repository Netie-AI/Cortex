"""RAG `/search` — dense + sparse (parallel), RRF, rerank, personalization."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from starlette.requests import Request

from netie.config import get_config
from netie.rag.fuser_rrf import FusedHit, fuse_dense_sparse
from netie.rag.personalization import personalized_score
from netie.rag.reranker import BGEReranker, RankedHit
from netie.rag.retriever_sparse import retrieve_sparse

log = logging.getLogger(__name__)

INTERACTION_COUNT_SQL = """
SELECT COUNT(*)::int AS c
FROM user_events
WHERE user_id::text = :user_id
"""


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    user_id: str | None = None


class SearchHit(BaseModel):
    listing_id: str
    fused_score: float
    rerank_score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    payload: dict[str, Any] | None = None


class SearchResponse(BaseModel):
    results: list[SearchHit]
    cold_start: bool


def register_search_routes(app: Any) -> None:
    from fastapi import APIRouter

    if not hasattr(app.state, "bge_reranker"):
        app.state.bge_reranker = BGEReranker()

    cfg = get_config()
    if getattr(app.state, "dense_retriever", None) is None and getattr(
        cfg, "qdrant_url", None
    ):
        try:
            from netie.rag.retriever_dense import build_dense_retriever

            app.state.dense_retriever = build_dense_retriever(
                qdrant_url=str(cfg.qdrant_url),
                collection_name=cfg.qdrant_collection_listings,
                embedder_model=cfg.embedder_model,
            )
        except Exception as exc:
            log.debug("Dense retriever skipped on startup (%s)", exc)

    router = APIRouter(tags=["search"])

    async def parse_query_stub(q: str) -> str:
        # TODO AI3.1 wire T1 query understanding
        await asyncio.sleep(0)
        return q.strip()

    @router.post("/search", response_model=SearchResponse)
    async def search(req: SearchRequest, request: Request) -> SearchResponse:
        engine = getattr(request.app.state, "db_engine", None)
        reranker: BGEReranker = request.app.state.bge_reranker
        dense_retriever = getattr(request.app.state, "dense_retriever", None)

        normalized = await parse_query_stub(req.query)

        interaction_count = await get_interaction_count(engine, req.user_id)
        cosine_resolver = getattr(
            request.app.state, "preference_cosine_resolver", None
        )
        collaborative_resolver = getattr(
            request.app.state, "preference_collaborative_resolver", None
        )
        preference_vec = getattr(request.app.state, "user_pref_vec", None)

        async def dense_task() -> list[Any]:
            if dense_retriever is None:
                return []
            return await dense_retriever.retrieve_dense_query(normalized, top_k=50)

        async def sparse_task() -> list[Any]:
            if engine is None:
                return []
            return await retrieve_sparse(normalized, top_k=50, engine=engine)

        dense_hits, sparse_hits = await asyncio.gather(dense_task(), sparse_task())

        fused = fuse_dense_sparse(dense_hits, sparse_hits, k=60, top_n=30)
        ranked = await reranker.rerank(normalized, fused, top_n=10)

        cold = req.user_id is None or interaction_count < 5
        if cold:
            final_ranked = ranked
        else:
            final_ranked = apply_personalization(
                ranked,
                interactions_count=interaction_count,
                user_pref_vec=preference_vec,
                cosine_resolver=cosine_resolver,
                collaborative_resolver=collaborative_resolver,
            )

        out = [
            SearchHit(
                listing_id=r.hit.listing_id,
                fused_score=r.hit.fused_score,
                rerank_score=r.rerank_score,
                dense_score=r.hit.dense_score,
                sparse_score=r.hit.sparse_score,
                payload=r.hit.payload,
            )
            for r in final_ranked
        ]
        return SearchResponse(results=out, cold_start=cold)

    app.include_router(router)


async def get_interaction_count(engine: Any | None, user_id: str | None) -> int:
    if engine is None or user_id is None:
        return 0
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            row = await conn.execute(
                text(INTERACTION_COUNT_SQL),
                {"user_id": user_id},
            )
            val = row.scalar_one_or_none()
            return int(val or 0)
    except Exception:
        return 0


def apply_personalization(
    ranked: list[RankedHit],
    *,
    interactions_count: int,
    user_pref_vec: list[float] | None = None,
    cosine_resolver: Callable[[str], float] | None = None,
    collaborative_resolver: Callable[[str], float] | None = None,
    gamma_one: float = 0.15,
    gamma_two: float = 0.10,
) -> list[RankedHit]:
    if interactions_count < 5:
        return ranked
    cos_fn = cosine_resolver or (lambda _lid: 0.0)
    col_fn = collaborative_resolver or (lambda _lid: 0.0)
    _ = user_pref_vec  # Future: similarity vs listing embedding derived from prefs
    out: list[RankedHit] = []
    for r in ranked:
        lid = r.hit.listing_id
        new_score = personalized_score(
            r.rerank_score,
            cos_fn(lid),
            col_fn(lid),
            interactions_count,
            gamma_one=gamma_one,
            gamma_two=gamma_two,
        )
        out.append(RankedHit(hit=r.hit, rerank_score=new_score))
    return out


__all__ = [
    "register_search_routes",
    "get_interaction_count",
    "apply_personalization",
]
