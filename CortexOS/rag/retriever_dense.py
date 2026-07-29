"""Async dense retrieval via Qdrant + BGE-M3 vectors."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from CortexOS.packaging import FeatureNotInstalled, require_extra

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DenseHit:
    doc_id: str
    score: float
    payload: dict[str, Any] | None = None


class DenseEmbedder(Protocol):
    dimension: int

    def encode_dense(self, text: str, *, normalize_embeddings: bool = True) -> list[float]: ...


def stable_point_uuid(listing_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"netie:listings:{listing_id}"))


def _qdrant_models():
    require_extra("rag", feature="qdrant")
    from qdrant_client.models import Distance, PointStruct, VectorParams

    return Distance, PointStruct, VectorParams


def _async_client_cls():
    require_extra("rag", feature="qdrant")
    try:
        from qdrant_client import AsyncQdrantClient as Client  # type: ignore
    except ImportError:
        try:
            from qdrant_client.async_qdrant_client import (  # type: ignore
                AsyncQdrantClient as Client,
            )
        except ImportError as exc:
            raise FeatureNotInstalled("rag", feature="qdrant", missing=("qdrant_client",)) from exc
    return Client


class DenseRetriever:
    """Single-vector cosine collection; vectors produced by ``BGEM3Embedder`` by default."""

    def __init__(
        self,
        qdrant_url: str,
        collection_name: str,
        embedder: DenseEmbedder,
        *,
        vector_size: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.qdrant_url = qdrant_url.rstrip("/")
        self.collection_name = collection_name
        self.embedder = embedder
        dim = vector_size if vector_size is not None else getattr(embedder, "dimension", None)
        if dim is None:
            raise ValueError("Provide ``vector_size`` or an embedder with ``dimension``.")
        self._vector_size = int(dim)
        self._client = client or _async_client_cls()(url=self.qdrant_url)

    async def close(self) -> None:
        closer = getattr(self._client, "close", None)
        if not callable(closer):
            return
        maybe = closer()
        if asyncio.iscoroutine(maybe):
            await maybe

    async def collection_exists(self) -> bool:
        meta = await self._client.get_collections()
        for c in meta.collections:
            if getattr(c, "name", None) == self.collection_name:
                return True
        return False

    async def ensure_collection(self) -> None:
        if await self.collection_exists():
            return
        Distance, _, VectorParams = _qdrant_models()
        await self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
        )

    async def upsert_document(
        self,
        *,
        listing_id: str,
        text: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.ensure_collection()
        vector = await asyncio.to_thread(self.embedder.encode_dense, text)
        _d, PointStruct, _vp = _qdrant_models()
        merged = dict(payload or {})
        merged.setdefault("listing_id", listing_id)
        await self._client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=stable_point_uuid(listing_id),
                    vector=vector,
                    payload=merged,
                ),
            ],
            wait=True,
        )

    async def retrieve_dense_query(self, query: str, top_k: int = 50) -> list[DenseHit]:
        if not await self.collection_exists():
            log.warning(
                "Qdrant collection %s not found — returning zero dense hits (cold deploy).",
                self.collection_name,
            )
            return []
        embedding = await asyncio.to_thread(self.embedder.encode_dense, query)
        hits = await self._client.search(
            collection_name=self.collection_name,
            query_vector=embedding,
            limit=top_k,
            with_payload=True,
        )
        out: list[DenseHit] = []
        for hit in hits:
            payload_obj = getattr(hit, "payload", None) or {}
            payload_dict = dict(payload_obj) if isinstance(payload_obj, dict) else {}
            pid = getattr(hit, "id", None)
            doc_id = str(payload_dict.get("listing_id") or pid or "")
            out.append(
                DenseHit(
                    doc_id=doc_id,
                    score=float(getattr(hit, "score", 0.0)),
                    payload=payload_dict or None,
                )
            )
        return out


def build_dense_retriever(
    *,
    qdrant_url: str,
    collection_name: str,
    embedder_model: str | None = None,
) -> DenseRetriever:
    from netie.nlp.embedder_bge import BGEM3Embedder

    embedder = BGEM3Embedder(model_name=embedder_model)
    return DenseRetriever(qdrant_url=qdrant_url, collection_name=collection_name, embedder=embedder)
