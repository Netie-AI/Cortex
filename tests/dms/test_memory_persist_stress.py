"""Persistent RawKnnStore stress: write, close, reopen, recall, scope, overwrite."""

from __future__ import annotations

import json

from CortexOS.memory.factory import get_store
from CortexOS.memory.store import MemoryRecord
from CortexOS.memory.stores.rawknn import RawKnnStore

DIM = 32
N = 256
REOPENS = 3
GRAPH = {
    "nodes": [
        {"id": "n1", "kind": "ingest"},
        {"id": "n2", "kind": "audit"},
    ],
    "edges": [{"from": "n1", "to": "n2"}],
}


def _vec(i: int, dim: int = DIM) -> list[float]:
    v = [0.0] * dim
    v[i % dim] = 1.0
    v[(i // dim) % dim] += 0.4
    v[-1] += (i + 1) / (N + 1)
    return v


def test_rawknn_256_records_survive_three_reopens(tmp_path):
    root = tmp_path / "knn"
    first = RawKnnStore(root, dim=DIM)
    recs = [
        MemoryRecord(
            id=f"rec-{i}",
            text=f"fact-{i} cluster-{i % DIM}",
            vector=_vec(i),
            collection=f"col-{i % 4}",
            scope="personal" if i % 2 == 0 else "company",
        )
        for i in range(N)
    ]
    assert first.upsert(recs) == N
    assert first.stats()["count"] == N
    first.close()

    for cycle in range(REOPENS):
        store = RawKnnStore(root, dim=DIM)
        try:
            assert store.stats()["count"] == N
            for i in (0, 17, 64, 255):
                hits = store.query(_vec(i), k=1)
                assert hits[0].id == f"rec-{i}", (cycle, i, [h.id for h in hits])
                assert f"cluster-{i % DIM}" in hits[0].text
        finally:
            store.close()


def test_rawknn_overwrite_and_scope_isolation(tmp_path):
    root = tmp_path / "knn2"
    store = RawKnnStore(root, dim=DIM)
    store.upsert(
        [
            MemoryRecord(id="same", text="old", vector=_vec(0), scope="personal"),
            MemoryRecord(id="co", text="company secret", vector=_vec(1), scope="company"),
        ]
    )
    store.close()

    later = RawKnnStore(root, dim=DIM)
    try:
        later.upsert([MemoryRecord(id="same", text="new constructor graph", vector=_vec(0), scope="personal")])
        hits = later.query(_vec(0), k=1, scope="personal")
        assert hits[0].id == "same"
        assert hits[0].text == "new constructor graph"
        personal = later.query(_vec(1), k=5, scope="personal")
        assert all(h.id != "co" for h in personal)
        company = later.query(_vec(1), k=1, scope="company")
        assert company[0].id == "co"
    finally:
        later.close()


def test_rawknn_recalls_constructor_graph_json(tmp_path):
    root = tmp_path / "knn3"
    blob = json.dumps(GRAPH)
    store = RawKnnStore(root, dim=DIM)
    store.upsert([MemoryRecord(id="graph-1", text=blob, vector=_vec(2), meta={"kind": "constructor"})])
    store.close()

    later = RawKnnStore(root, dim=DIM)
    try:
        hit = later.query(_vec(2), k=1)[0]
        assert json.loads(hit.text) == GRAPH
        assert hit.meta["kind"] == "constructor"
    finally:
        later.close()


def test_factory_rawknn_uses_env_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_MEMORY_BACKEND", "rawknn")
    monkeypatch.setenv("CORTEX_MEMORY_ROOT", str(tmp_path / "env-knn"))
    monkeypatch.setenv("CORTEX_MEMORY_DIM", str(DIM))
    store = get_store()
    try:
        store.upsert([MemoryRecord(id="env-1", text="from factory", vector=_vec(0))])
    finally:
        store.close()
    reopened = get_store()
    try:
        assert reopened.query(_vec(0), k=1)[0].id == "env-1"
    finally:
        reopened.close()


def test_memory_api_survives_store_reopen(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("CORTEX_MEMORY_BACKEND", "rawknn")
    monkeypatch.setenv("CORTEX_MEMORY_ROOT", str(tmp_path / "api-knn"))
    monkeypatch.setenv("CORTEX_MEMORY_DIM", "64")
    from CortexOS.api import memory_routes
    from CortexOS.memory.factory import get_store as fresh_store

    # setattr, not plain assignment: this rebinds a module global that other
    # suites read, and monkeypatch puts the original store back on teardown.
    monkeypatch.setattr(memory_routes, "_STORE", fresh_store())
    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    vec = [1.0] + [0.0] * 63
    up = client.post(
        "/api/memory/upsert",
        json={"records": [{"id": "persist-1", "text": "constructor canvas", "vector": vec}]},
    )
    assert up.status_code == 200, up.text
    assert up.json()["upserted"] == 1
    memory_routes._STORE.close()
    monkeypatch.setattr(memory_routes, "_STORE", fresh_store())
    q = client.post("/api/memory/query", json={"vector": vec, "k": 1})
    assert q.status_code == 200, q.text
    hits = q.json()["hits"]
    assert hits[0]["id"] == "persist-1"
    assert "constructor" in hits[0]["text"]
    memory_routes._STORE.close()
