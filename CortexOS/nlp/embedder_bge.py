"""
Dense embeddings via BGE-M3 (sentence-transformers).

Default ``BAAI/bge-m3`` matches BM/EN/中文 mixed queries without a translation step.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

_DEFAULT_MODEL = "BAAI/bge-m3"


def default_embedder_model_name() -> str:
    return os.environ.get("EMBEDDER_MODEL") or os.environ.get("NETIE_EMBEDDER_MODEL") or _DEFAULT_MODEL


class BGEM3Embedder:
    """Lazy-load SentenceTransformer; exposes ``dimension`` for Qdrant collection sizing."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        device: str | None = None,
        model_kwargs: dict[str, Any] | None = None,
        max_seq_length: int | None = None,
    ) -> None:
        """``model_kwargs`` are extra keyword arguments for the
        ``SentenceTransformer`` constructor. ``max_seq_length``, when given,
        caps the tokens each text is embedded from. A ``model_name`` passed
        explicitly is used as-is; ``EMBEDDER_MODEL`` applies only without one.
        """
        from CortexOS.packaging import require_extra

        require_extra("rag", feature="BGEM3Embedder", modules=("sentence_transformers",))
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or default_embedder_model_name()
        self._model = SentenceTransformer(self.model_name, device=device, **(model_kwargs or {}))
        if max_seq_length is not None:
            self._model.max_seq_length = int(max_seq_length)

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

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        """Unit-length vectors, one row per text."""
        if not texts:
            return np.zeros((0, self.dimension), dtype=float)
        vecs = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            batch_size=32,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=float).reshape(len(texts), -1)


def embed_text(text: str, model_name: str | None = None) -> list[float]:
    """Shim for one-off calls; prefer a single shared ``BGEM3Embedder`` in services."""
    return BGEM3Embedder(model_name=model_name).encode_dense(text)


__all__ = ["BGEM3Embedder", "default_embedder_model_name", "embed_text"]
