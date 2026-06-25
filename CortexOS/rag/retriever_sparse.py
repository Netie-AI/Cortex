from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SparseHit:
    listing_id: str
    score: float
    payload: dict[str, Any] | None = None


class SparseRetriever:
    """
    Postgres FTS retriever using:
      plainto_tsquery('simple', :query) + ts_rank_cd(...)
    """

    SEARCH_SQL = """
        SELECT
          id::text AS listing_id,
          ts_rank_cd(fts_doc, plainto_tsquery('simple', :query)) AS score,
          title,
          description,
          project_name,
          address,
          postcode
        FROM listings
        WHERE fts_doc @@ plainto_tsquery('simple', :query)
        ORDER BY score DESC
        LIMIT :limit
    """

    UPSERT_FTS_SQL = """
        UPDATE listings
        SET fts_doc = to_tsvector(
          'simple',
          coalesce(title,'') || ' ' ||
          coalesce(description,'') || ' ' ||
          coalesce(project_name,'') || ' ' ||
          coalesce(address,'') || ' ' ||
          coalesce(postcode,'')
        )
        WHERE id = :listing_id
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def retrieve_sparse(self, query: str, top_k: int = 50) -> list[SparseHit]:
        normalized = query.strip()
        if not normalized:
            return []
        text = _sql_text
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(self.SEARCH_SQL),
                {"query": normalized, "limit": int(top_k)},
            )
            raw = rows.mappings().all()

        hits: list[SparseHit] = []
        for row in raw:
            listing_id = str(row.get("listing_id", ""))
            payload = {
                "title": row.get("title"),
                "description": row.get("description"),
                "project_name": row.get("project_name"),
                "address": row.get("address"),
                "postcode": row.get("postcode"),
            }
            hits.append(
                SparseHit(
                    listing_id=listing_id,
                    score=float(row.get("score") or 0.0),
                    payload=payload,
                )
            )
        return hits

    async def refresh_listing_fts(self, listing_id: str) -> None:
        text = _sql_text
        async with self._engine.begin() as conn:
            await conn.execute(text(self.UPSERT_FTS_SQL), {"listing_id": listing_id})


async def retrieve_sparse(query: str, top_k: int = 50, *, engine: Any) -> list[SparseHit]:
    return await SparseRetriever(engine).retrieve_sparse(query, top_k=top_k)


def _sql_text(sql: str):
    try:
        from sqlalchemy import text

        return text(sql)
    except Exception:
        # Allows offline unit tests with fake engines that accept raw SQL strings.
        return sql
