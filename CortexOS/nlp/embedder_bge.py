"""
Dense embeddings via BGE-M3 (sentence-transformers).

Default ``BAAI/bge-m3`` matches BM/EN/中文 mixed queries without a translation step.
"""

from __future__ import annotations

import os

import numpy as np

_DEFAULT_MODEL = "BAAI/bge-m3"


def default_embedder_model_name() -> str:
    return os.environ.get("EMBEDDER_MODEL") or os.environ.get("NETIE_EMBEDDER_MODEL") or _DEFAULT_MODEL


class BGEM3Embedder:
    """Lazy-load SentenceTransformer; exposes ``dimension`` for Qdrant collection sizing."""

    def __init__(self, model_name: str | None = None, *, device: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or default_embedder_model_name()
        self._model = SentenceTransformer(self.model_name, device=device)

        dim_fn = getattr(self._model, "get_sentence_embedding_dimension", None)
        if callable(dim_fn):
            self.dimension = int(dim_fn())
        else:
            vec = self._model.encode(
                ["."],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            self.dimension = int(np.asarray(vec, dtype=float).reshape(-1).shape[0])

    def encode_dense(self, text: str, *, normalize_embeddings: bool = True) -> list[float]:
        if not text or not text.strip():
            return [0.0] * self.dimension
        vec = self._model.encode(
            text.strip(),
            normalize_embeddings=normalize_embeddings,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        flat = np.asarray(vec, dtype=float).reshape(-1)
        return flat.astype(float).tolist()


def embed_text(text: str, model_name: str | None = None) -> list[float]:
    """Shim for one-off calls; prefer a single shared ``BGEM3Embedder`` in services."""
    return BGEM3Embedder(model_name=model_name).encode_dense(text)


__all__ = ["BGEM3Embedder", "default_embedder_model_name", "embed_text"]
