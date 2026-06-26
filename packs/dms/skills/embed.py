"""Deterministic local trigger embedding (T0 — no BIG_API, no model load)."""

from __future__ import annotations

import hashlib
import math
import re

_DIM = 32


def local_embed(text: str, *, dim: int = _DIM) -> list[float]:
    """Hash n-gram bag → L2-normalized vector for cosine skill matching."""
    normalized = re.sub(r"\s+", " ", (text or "").lower().strip())
    if not normalized:
        return [0.0] * dim

    vec = [0.0] * dim
    tokens = normalized.split()
    for token in tokens:
        h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    for i in range(len(normalized) - 1):
        bigram = normalized[i : i + 2]
        h = int(hashlib.sha256(bigram.encode()).hexdigest(), 16)
        vec[h % dim] += 0.5

    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
