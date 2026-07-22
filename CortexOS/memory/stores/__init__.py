"""Netie Memory store backends (import as ``netie.memory.stores``).

Implementations of the M0 ``VectorStore`` protocol, selected by
``netie.memory.store.select_store``:

  rawknn     — mmap brute-force KNN, exact, ~0 idle RAM (<=100k vectors)
  sqlitevec  — sqlite-vec personal default (<=500k with int8 quant)   [planned]
  qdrant     — on-disk HNSW for business scale                        [planned]

Grounded in docs/research/findings/D1_D5_D6_vector_memory.md and
A1_A2_mmap_pagecache.md.
"""
