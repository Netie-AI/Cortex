"""RAG package — dense/sparse retrieval, fusion, rerank.

Heavy third-party deps (qdrant-client, tantivy, sentence-transformers) live in
the ``rag`` optional extra. Submodules load lazily so a base install can import
this package without those extras present.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "rrf_fuse",
    "fuse_dense_sparse",
    "FusedHit",
    "BGEReranker",
    "RankedHit",
    "doc_text_from_fused",
    "personalized_score",
    "DenseHit",
    "DenseRetriever",
    "build_dense_retriever",
    "stable_point_uuid",
    "SparseHit",
    "SparseRetriever",
]

_LAZY: dict[str, tuple[str, str]] = {
    "rrf_fuse": (".fuser_rrf", "rrf_fuse"),
    "fuse_dense_sparse": (".fuser_rrf", "fuse_dense_sparse"),
    "FusedHit": (".fuser_rrf", "FusedHit"),
    "BGEReranker": (".reranker", "BGEReranker"),
    "RankedHit": (".reranker", "RankedHit"),
    "doc_text_from_fused": (".reranker", "doc_text_from_fused"),
    "personalized_score": (".personalization", "personalized_score"),
    "DenseHit": (".retriever_dense", "DenseHit"),
    "DenseRetriever": (".retriever_dense", "DenseRetriever"),
    "build_dense_retriever": (".retriever_dense", "build_dense_retriever"),
    "stable_point_uuid": (".retriever_dense", "stable_point_uuid"),
    "SparseHit": (".retriever_sparse", "SparseHit"),
    "SparseRetriever": (".retriever_sparse", "SparseRetriever"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from CortexOS.packaging import require_extra

    require_extra("rag", feature=name)
    mod_name, attr = _LAZY[name]
    import importlib

    mod = importlib.import_module(mod_name, __name__)
    value = getattr(mod, attr)
    globals()[name] = value
    return value
