"""Lightweight lexical RAG helpers for dag_runner RAG_* nodes (no Qdrant required)."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-./]{1,}")


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def overlap_score(query: str, text: str) -> float:
    q = Counter(tokens(query))
    if not q:
        return 0.0
    d = Counter(tokens(text))
    dot = sum(q[t] * d.get(t, 0) for t in q)
    if not dot:
        return 0.0
    qn = math.sqrt(sum(v * v for v in q.values()))
    dn = math.sqrt(sum(v * v for v in d.values())) or 1.0
    return float(dot / (qn * dn))


def retrieve(
    query: str,
    corpus: list[dict[str, Any]],
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Score corpus docs by lexical overlap. Each doc: {id, text, ...}."""
    hits: list[dict[str, Any]] = []
    for doc in corpus or []:
        if not isinstance(doc, dict):
            continue
        text = str(doc.get("text") or "")
        score = overlap_score(query, text)
        if score <= 0:
            continue
        row = dict(doc)
        row["score"] = score
        row.setdefault("id", row.get("doc_id") or row.get("chunk_id") or len(hits))
        hits.append(row)
    hits.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
    return hits[: max(1, int(top_k))]


def rerank(query: str, hits: list[dict[str, Any]], *, top_n: int = 8) -> list[dict[str, Any]]:
    """Re-score with overlap (cross-encoder optional later). Preserves shape."""
    rescored: list[dict[str, Any]] = []
    for h in hits or []:
        row = dict(h)
        text = str(row.get("text") or row.get("excerpt") or "")
        row["rerank_score"] = overlap_score(query, text)
        row["score"] = float(row["rerank_score"])
        rescored.append(row)
    rescored.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
    return rescored[: max(1, int(top_n))]


def answer_from_hits(query: str, hits: list[dict[str, Any]], *, max_chars: int = 1200) -> dict[str, Any]:
    """Extractive answer: concatenate top excerpts."""
    parts: list[str] = []
    citations: list[dict[str, Any]] = []
    for i, h in enumerate(hits or [], start=1):
        text = str(h.get("text") or h.get("excerpt") or "").strip()
        if not text:
            continue
        excerpt = text[:280]
        parts.append(f"[{i}] {excerpt}")
        citations.append(
            {
                "n": i,
                "id": h.get("id") or h.get("doc_id"),
                "score": h.get("score"),
                "excerpt": excerpt,
            }
        )
        if sum(len(p) for p in parts) >= max_chars:
            break
    body = "\n".join(parts) if parts else "No relevant documents found."
    return {
        "query": query,
        "answer": body,
        "citations": citations,
        "n_hits": len(hits or []),
    }


def coverage(hits: list[dict[str, Any]]) -> float:
    if not hits:
        return 0.0
    return min(1.0, sum(float(h.get("score") or 0) for h in hits[:5]) / 2.0)
