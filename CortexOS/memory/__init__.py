"""Netie Memory — persistent dual-brain memory plane (import as ``netie.memory``).

Named ``memory`` (not ``brain``) to avoid colliding with the existing DMS
generative brain at ``netie.api.brain_routes`` / ``/dms/brain/*``.

Tiered, always-learning, hardware-aware store selection:
  raw-mmap brute-force KNN (<~10k) → sqlite-vec (personal, <500k, ~0 idle RAM)
  → Qdrant / pgvector (business scale). Compression: scalar-quant + Matryoshka.
  Scopes: personal (laptop-local, hidden) + company (role-labelled, collaborative).

See docs/strategy/NETIE_ENGINE_UP_PLAN.md §5c/§6-M and the research brief §D–F.
"""
