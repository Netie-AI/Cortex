"""M0 — Netie Memory store interface (registry-first unblocker).

One `VectorStore` protocol that every backend (raw-mmap KNN, sqlite-vec, Qdrant,
pgvector, Mongo) implements, plus:
  * `InMemoryStore` — a working brute-force cosine reference impl. It IS the
    `<~10k` raw-KNN tier's math and the shared conformance baseline (plan §5c/D5).
  * `select_store()` — hardware/size-aware auto-selection stub (brute-force below
    a crossover N, index above) — the number comes from research brief D5.
  * `conformance_check()` — the shared round-trip every impl must pass.

Zero hard deps (stdlib + typing). Real backends live in
`netie.memory.stores.*` and get wired as brief §D findings land.

Import as ``netie.memory.store``.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable, Literal, Protocol, runtime_checkable

Scope = Literal["personal", "company"]
Tier = Literal["hot", "warm", "cold"]

# Auto-select crossover per D5 finding (docs/research/findings/D1_D5_D6_vector_memory.md):
# brute force wins <=100k; 100k-1M is access-pattern dependent; HNSW/IVF wins >1M.
BRUTE_FORCE_MAX = 100_000


@dataclass(slots=True)
class MemoryRecord:
    id: str
    text: str
    vector: list[float] | None = None
    meta: dict = field(default_factory=dict)
    scope: Scope = "personal"
    collection: str = "default"
    role: str | None = None          # company scope: writer's assigned role/position
    tier: Tier = "warm"
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Hit:
    id: str
    score: float
    text: str
    meta: dict = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    """The one interface. Every store (mmap/sqlite-vec/Qdrant/pgvector) implements it."""

    def upsert(self, records: Iterable[MemoryRecord]) -> int: ...
    def query(self, vector: list[float], *, k: int = 5,
              scope: Scope | None = None, collection: str | None = None) -> list[Hit]: ...
    def evict(self, *, policy: str = "cold", max_keep: int | None = None) -> int: ...
    def stats(self) -> dict: ...


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryStore:
    """Brute-force cosine reference impl — the <10k raw-KNN tier + conformance base.

    Exact recall (ground truth for `bench/vector_recall.py`). Real persistence is
    a subclass that mmaps `vectors.bin` + a SQLite/RocksDB chunk store (brief D6).
    """

    def __init__(self) -> None:
        self._rows: dict[str, MemoryRecord] = {}

    def upsert(self, records: Iterable[MemoryRecord]) -> int:
        n = 0
        for r in records:
            self._rows[r.id] = r
            n += 1
        return n

    def query(self, vector: list[float], *, k: int = 5,
              scope: Scope | None = None, collection: str | None = None) -> list[Hit]:
        cands = [
            r for r in self._rows.values()
            if r.vector
            and (scope is None or r.scope == scope)
            and (collection is None or r.collection == collection)
        ]
        scored = sorted(
            (Hit(r.id, cosine(vector, r.vector or []), r.text, r.meta) for r in cands),
            key=lambda h: -h.score,
        )
        return scored[:k]

    def evict(self, *, policy: str = "cold", max_keep: int | None = None) -> int:
        # Reference policy: drop cold tier first, then oldest, down to max_keep.
        if max_keep is None or len(self._rows) <= max_keep:
            return 0
        ordered = sorted(
            self._rows.values(),
            key=lambda r: (0 if r.tier == "cold" else 1 if r.tier == "warm" else 2, r.created_at),
        )
        drop = len(self._rows) - max_keep
        for r in ordered[:drop]:
            del self._rows[r.id]
        return drop

    def stats(self) -> dict:
        return {"count": len(self._rows), "backend": "in_memory_bruteforce"}


def select_store(collection_size: int, hardware: dict | None = None) -> str:
    """Auto-pick the store family by size (crossover from brief D5) + hardware.

    Returns a store id; the factory that maps id→impl is filled as backends land.
    """
    if collection_size <= BRUTE_FORCE_MAX:
        return "rawknn"          # mmap brute-force — exact, ~0 idle RAM (D5)
    if collection_size <= 500_000:
        return "sqlitevec"       # personal default; needs int8 quant above ~100k (D1)
    return "qdrant"              # business scale, low-RAM on-disk HNSW


def conformance_check(store: VectorStore) -> dict:
    """The shared round-trip every store impl must pass (upsert → query → evict)."""
    recs = [
        MemoryRecord(id="a", text="alpha", vector=[1.0, 0.0, 0.0]),
        MemoryRecord(id="b", text="beta", vector=[0.0, 1.0, 0.0]),
        MemoryRecord(id="c", text="gamma", vector=[0.9, 0.1, 0.0]),
    ]
    assert store.upsert(recs) == 3
    hits = store.query([1.0, 0.0, 0.0], k=2)
    assert hits and hits[0].id == "a", "nearest must be exact match"
    assert hits[1].id == "c", "second nearest must be the close vector"
    assert store.stats()["count"] == 3
    return {"ok": True, "top": hits[0].id, "second": hits[1].id, "stats": store.stats()}


def self_check() -> dict:
    return conformance_check(InMemoryStore())


if __name__ == "__main__":  # pragma: no cover
    import json
    print(json.dumps(self_check(), indent=2))
