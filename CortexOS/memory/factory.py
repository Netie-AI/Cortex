"""Memory-store selection for API and application wiring."""

from __future__ import annotations

import os
from pathlib import Path

from netie.memory.store import InMemoryStore, VectorStore
from netie.memory.stores.rawknn import RawKnnStore

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_RAWKNN_ROOT = _PROJECT_ROOT / "data" / "memory" / "rawknn"
_DEFAULT_RAWKNN_DIM = 64


def get_store(root: str | Path | None = None, dim: int | None = None) -> VectorStore:
    """Return the configured memory store.

    The persistent mmap backend is opt-in so test and transient API processes do
    not leave state in the checkout. Set either memory backend variable to
    ``rawknn`` to select it.
    """
    backend = os.getenv("CORTEX_MEMORY_BACKEND", "").lower()
    store_name = os.getenv("CORTEX_MEMORY_STORE", "").lower()
    if backend == "rawknn" or store_name == "rawknn":
        env_root = os.getenv("CORTEX_MEMORY_ROOT")
        env_dim = os.getenv("CORTEX_MEMORY_DIM")
        chosen_dim = dim
        if chosen_dim is None and env_dim:
            chosen_dim = int(env_dim)
        if chosen_dim is None:
            chosen_dim = _DEFAULT_RAWKNN_DIM
        return RawKnnStore(root or env_root or _DEFAULT_RAWKNN_ROOT, dim=chosen_dim)
    return InMemoryStore()
