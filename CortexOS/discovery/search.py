"""Fast BM25 (+ light lexical boosts) over discovery catalogs.

No sentence-transformers dependency — discovery must stay cheap and reliable
even when dense models are mocked or unavailable in tests.
"""

from __future__ import annotations

import re
from typing import Sequence

from CortexOS.discovery.models import DiscoveryHit, RefItem

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover
    BM25Okapi = None  # type: ignore[misc, assignment]


_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+.-]*")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _doc_text(item: RefItem) -> str:
    return " ".join(
        [
            item.name,
            item.description,
            item.category,
            item.source,
            " ".join(item.tags),
            item.kind,
            item.install_hint,
        ]
    )


def _reputation_boost(item: RefItem) -> float:
    if item.reputation in ("official", "local"):
        return 0.35
    if item.kind == "local_skill":
        return 0.4
    if (item.installs_hint or 0) >= 1000:
        return 0.2
    return 0.0


def _kind_priority(kind: str) -> float:
    # Skills first policy.
    return {
        "local_skill": 0.5,
        "skill": 0.35,
        "subagent": 0.15,
        "toolkit": 0.1,
        "mcp": 0.05,
    }.get(kind, 0.0)


class CatalogIndex:
    def __init__(self, items: Sequence[RefItem]):
        self.items = list(items)
        corpus = [tokenize(_doc_text(it)) for it in self.items]
        self._bm25 = BM25Okapi(corpus) if BM25Okapi and corpus else None

    def query(self, goal: str, *, top_k: int = 8, kinds: set[str] | None = None) -> list[DiscoveryHit]:
        q = (goal or "").strip()
        if not q or not self.items:
            return []

        tokens = tokenize(q)
        if self._bm25 is not None and tokens:
            raw_scores = list(self._bm25.get_scores(tokens))
        else:
            # Fallback: simple token overlap when rank_bm25 missing.
            qset = set(tokens)
            raw_scores = []
            for it in self.items:
                doc = set(tokenize(_doc_text(it)))
                raw_scores.append(float(len(qset & doc)))

        hits: list[DiscoveryHit] = []
        for item, raw in zip(self.items, raw_scores):
            if kinds is not None and item.kind not in kinds:
                continue
            if raw <= 0 and not _substring_hit(q, item):
                continue
            score = float(raw) + _reputation_boost(item) + _kind_priority(item.kind)
            if _substring_hit(q, item):
                score += 0.75
            why = _why(q, item, score)
            hits.append(
                DiscoveryHit(
                    id=item.id,
                    kind=item.kind,
                    name=item.name,
                    description=item.description,
                    score=round(score, 4),
                    url=item.url,
                    source=item.source,
                    category=item.category,
                    tags=list(item.tags),
                    install_hint=item.install_hint,
                    reputation=item.reputation,
                    why=why,
                )
            )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


def _substring_hit(goal: str, item: RefItem) -> bool:
    g = goal.lower()
    return any(
        part and part in g
        for part in (item.name.lower(), *(t.lower() for t in item.tags))
    ) or any(tok in _doc_text(item).lower() for tok in tokenize(goal) if len(tok) > 3)


def _why(goal: str, item: RefItem, score: float) -> str:
    bits = [f"matched goal '{goal[:80]}'", f"kind={item.kind}", f"source={item.source}"]
    if item.reputation == "official":
        bits.append("trusted official source")
    if item.kind == "local_skill":
        bits.append("already available in Cortex")
    bits.append(f"score={score:.2f}")
    return "; ".join(bits)
