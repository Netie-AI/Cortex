from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FusedHit:
    listing_id: str
    fused_score: float
    payload: dict[str, Any] | None = None
    dense_score: float | None = None
    sparse_score: float | None = None


def rrf_fuse(rank_lists: list[list[str]], k: int = 60, top_n: int = 30) -> list[tuple[str, float]]:
    """
    Reciprocal rank fusion across dense/sparse retrieval lists.
    """
    scores: dict[str, float] = defaultdict(float)
    for rank_list in rank_lists:
        for idx, doc_id in enumerate(rank_list):
            scores[doc_id] += 1.0 / (k + idx + 1)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return ranked[:top_n]


def fuse_dense_sparse(
    dense_hits: list[Any],
    sparse_hits: list[Any],
    *,
    k: int = 60,
    top_n: int = 30,
) -> list[FusedHit]:
    """
    Reciprocal Rank Fusion for two ranked lists (dense + sparse).
    Expected fields:
      dense -> listing_id|doc_id, score, payload?
      sparse -> listing_id|doc_id, score, payload?
    """
    rank_scores: dict[str, float] = defaultdict(float)
    dense_map: dict[str, Any] = {}
    sparse_map: dict[str, Any] = {}

    for idx, hit in enumerate(dense_hits, start=1):
        listing_id = str(getattr(hit, "listing_id", None) or getattr(hit, "doc_id", ""))
        if not listing_id:
            continue
        rank_scores[listing_id] += 1.0 / (k + idx)
        dense_map[listing_id] = hit

    for idx, hit in enumerate(sparse_hits, start=1):
        listing_id = str(getattr(hit, "listing_id", None) or getattr(hit, "doc_id", ""))
        if not listing_id:
            continue
        rank_scores[listing_id] += 1.0 / (k + idx)
        sparse_map[listing_id] = hit

    ranked = sorted(rank_scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
    out: list[FusedHit] = []
    for listing_id, fused_score in ranked:
        d = dense_map.get(listing_id)
        s = sparse_map.get(listing_id)
        payload = None
        if d is not None:
            payload = getattr(d, "payload", None)
        if payload is None and s is not None:
            payload = getattr(s, "payload", None)
        out.append(
            FusedHit(
                listing_id=listing_id,
                fused_score=float(fused_score),
                payload=payload,
                dense_score=(float(getattr(d, "score", 0.0)) if d is not None else None),
                sparse_score=(float(getattr(s, "score", 0.0)) if s is not None else None),
            )
        )
    return out
