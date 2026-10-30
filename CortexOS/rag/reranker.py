"""
Cross-encoder reranking (BGE-reranker-v2-m3) with asyncio semaphore serialization for GPU contention.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from netie.rag.fuser_rrf import FusedHit

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RankedHit:
    hit: FusedHit
    rerank_score: float


def doc_text_from_fused(hit: FusedHit) -> str:
    payload = dict(hit.payload or {})
    explicit = payload.get("text") or ""
    if explicit:
        return str(explicit)
    parts = [
        payload.get("title"),
        payload.get("description"),
        payload.get("project_name"),
        payload.get("address"),
        payload.get("postcode"),
    ]
    return " ".join(str(p) for p in parts if p).strip()


@dataclass(slots=True)
class RerankResult:
    doc_id: str
    score: float


class BGEReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        max_concurrent: int = 1,
        device: str | None = None,
        model_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """``model_kwargs`` are extra keyword arguments for the ``CrossEncoder``
        constructor. They are passed only when given, so a caller that names
        none constructs exactly as before."""
        self._sem = asyncio.Semaphore(max_concurrent)
        self._model_name = model_name
        self._device = device
        self._model_kwargs = dict(model_kwargs) if model_kwargs else None
        self._cross_encoder: Any | None = None
        self._load_failed = False

    def _get_cross_encoder(self) -> Any | None:
        if self._load_failed:
            return None
        if self._cross_encoder is not None:
            return self._cross_encoder
        try:
            from sentence_transformers import CrossEncoder

            if self._model_kwargs:
                self._cross_encoder = CrossEncoder(
                    self._model_name, device=self._device, **self._model_kwargs
                )
            else:
                self._cross_encoder = CrossEncoder(self._model_name, device=self._device)
            return self._cross_encoder
        except Exception as exc:
            log.debug(
                "BGE reranker unavailable (%s); preserving RRF order with rerank_score=0.0",
                exc,
            )
            self._load_failed = True
            return None

    def ensure_loaded(self) -> bool:
        """Load the cross-encoder now. False when it could not be loaded."""
        return self._get_cross_encoder() is not None

    def score(self, query: str, texts: list[str]) -> list[float] | None:
        """One relevance score per text, or None when no usable score exists.

        None (with a WARNING) when the model did not load, when ``predict``
        raises, when it returns the wrong number of scores, or when any score
        is not finite. Never silently zero.
        """
        if not texts:
            return []
        model = self._get_cross_encoder()
        if model is None:
            log.warning("BGE reranker not loaded; no rerank scores")
            return None
        try:
            raw = model.predict([(query, t) for t in texts], show_progress_bar=False)
            arr = np.asarray(raw, dtype=float).reshape(-1)
        except Exception as exc:
            log.warning("BGE reranker predict failed: %s", type(exc).__name__)
            return None
        if arr.shape[0] != len(texts) or not bool(np.all(np.isfinite(arr))):
            log.warning("BGE reranker returned unusable scores (n=%d for %d texts)", arr.shape[0], len(texts))
            return None
        return [float(x) for x in arr]

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        model = self._get_cross_encoder()
        if model is None:
            return [0.0] * len(pairs)
        raw = model.predict(pairs, show_progress_bar=False)
        arr = np.asarray(raw, dtype=float).reshape(-1)
        return [float(x) for x in arr]

    async def rerank(self, query: str, hits: list[FusedHit], top_n: int = 10) -> list[RankedHit]:
        if not hits:
            return []
        async with self._sem:
            pairs = [(query, doc_text_from_fused(h)) for h in hits]
            scores = await asyncio.to_thread(self._score_pairs, pairs)

        if all(s == 0.0 for s in scores):
            return [RankedHit(hit=h, rerank_score=0.0) for h in hits[:top_n]]

        ranked = sorted(
            zip(scores, hits, strict=False),
            key=lambda x: x[0],
            reverse=True,
        )
        return [RankedHit(hit=h, rerank_score=float(s)) for s, h in ranked[:top_n]]


def rerank(query: str, candidate_doc_ids: list[str], top_k: int = 10) -> list[RerankResult]:
    """Legacy placeholder API (doc-id only). Prefer ``BGEReranker``."""
    return [
        RerankResult(doc_id=doc_id, score=1.0) for doc_id in candidate_doc_ids[:top_k]
    ]


__all__ = ["BGEReranker", "RankedHit", "RerankResult", "doc_text_from_fused", "rerank"]
