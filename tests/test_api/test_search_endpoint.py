import pytest

pytest.importorskip("fastapi")

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import netie.api.search as search_mod
from netie.api.app import create_app
from netie.rag.fuser_rrf import FusedHit
from netie.rag.reranker import RankedHit
from netie.rag.retriever_dense import DenseHit


@pytest.fixture(autouse=True)
def _patch_sparse_empty(monkeypatch) -> None:
    async def sparse_stub(*args, **kwargs):  # noqa: ARG001
        return []

    monkeypatch.setattr(search_mod, "retrieve_sparse", sparse_stub)


@pytest.fixture()
def fused_one() -> list[FusedHit]:
    return [
        FusedHit(
            listing_id="L1",
            fused_score=0.62,
            payload={"title": "Condo KL"},
            dense_score=0.9,
            sparse_score=1.2,
        ),
    ]


@pytest.fixture()
def fused_two() -> list[FusedHit]:
    return [
        FusedHit("L1", 0.62, payload={"title": "one"}, dense_score=0.9, sparse_score=None),
        FusedHit("L2", 0.55, payload={"title": "two"}, dense_score=0.5, sparse_score=None),
    ]


def _fuse_stub_chain(fused: list[FusedHit]):
    def _fuse(dense_hits, sparse_hits, *, k=60, top_n=30):  # noqa: ARG001
        del dense_hits, sparse_hits, k
        return fused[:top_n]

    return _fuse


def test_logged_user_below_five_personalization_bypass(
    monkeypatch, fused_one: list[FusedHit]
) -> None:
    monkeypatch.setattr(search_mod, "fuse_dense_sparse", _fuse_stub_chain(fused_one))

    async def icount_4(*args, **kwargs):  # noqa: ARG001
        return 4

    monkeypatch.setattr(search_mod, "get_interaction_count", icount_4)

    app = create_app()

    async def deterministic_rerank(query, fused, top_n):  # noqa: ARG001
        return [RankedHit(hit=h, rerank_score=7.77) for h in fused[:top_n]]

    dr = AsyncMock()
    dr.retrieve_dense_query = AsyncMock(
        return_value=[DenseHit(doc_id="L1", score=0.9, payload={"title": "Condo KL"})]
    )
    app.state.dense_retriever = dr
    app.state.bge_reranker.rerank = deterministic_rerank

    client = TestClient(app)
    resp = client.post("/search", json={"query": "near mid valley", "user_id": "u_1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cold_start"] is True
    assert body["results"][0]["listing_id"] == "L1"
    assert body["results"][0]["rerank_score"] == pytest.approx(7.77)


def test_anonymous_always_cold_start(monkeypatch, fused_two: list[FusedHit]) -> None:
    monkeypatch.setattr(search_mod, "fuse_dense_sparse", _fuse_stub_chain(fused_two))

    app = create_app()

    async def deterministic_rerank(query, fused, top_n):  # noqa: ARG001
        return [RankedHit(hit=h, rerank_score=3.33) for h in fused[:top_n]]

    dr = AsyncMock()
    dr.retrieve_dense_query = AsyncMock(return_value=[])
    app.state.dense_retriever = dr
    app.state.bge_reranker.rerank = deterministic_rerank

    client = TestClient(app)
    resp = client.post("/search", json={"query": "find me a flat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cold_start"] is True
    assert len(body["results"]) == 2


def test_warm_user_personalization_boosts(monkeypatch, fused_one: list[FusedHit]) -> None:
    monkeypatch.setattr(search_mod, "fuse_dense_sparse", _fuse_stub_chain(fused_one))

    async def icount_11(*args, **kwargs):  # noqa: ARG001
        return 11

    monkeypatch.setattr(search_mod, "get_interaction_count", icount_11)

    app = create_app()

    async def deterministic_rerank(query, fused, top_n):  # noqa: ARG001
        return [RankedHit(hit=h, rerank_score=7.77) for h in fused[:top_n]]

    dr = AsyncMock()
    dr.retrieve_dense_query = AsyncMock(return_value=[])
    app.state.dense_retriever = dr
    app.state.bge_reranker.rerank = deterministic_rerank
    app.state.preference_cosine_resolver = lambda _lid: 10.0
    app.state.preference_collaborative_resolver = lambda _lid: 0.0

    client = TestClient(app)
    resp = client.post("/search", json={"query": "condo", "user_id": "u_w"})
    body = resp.json()
    assert body["cold_start"] is False
    assert body["results"][0]["rerank_score"] == pytest.approx(
        7.77 + (0.15 * 10.0) + (0.10 * 0.0)
    )
