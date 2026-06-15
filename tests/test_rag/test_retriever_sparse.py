from dataclasses import dataclass

import pytest

from netie.rag.retriever_sparse import SparseRetriever


@dataclass
class _FakeResult:
    rows: list[dict]

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, stmt, params):
        self.executed.append((str(stmt), dict(params)))
        return _FakeResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []
        self.conn = _FakeConn(self._rows)

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


@pytest.mark.asyncio
async def test_sparse_retriever_maps_hits():
    eng = _FakeEngine(
        rows=[
            {
                "listing_id": "l_1",
                "score": 0.88,
                "title": "Condo near Mid Valley",
                "description": "2 bed",
                "project_name": "Pavilion",
                "address": "KL",
                "postcode": "59000",
            }
        ]
    )
    retriever = SparseRetriever(eng)
    hits = await retriever.retrieve_sparse("mid valley", top_k=5)
    assert len(hits) == 1
    assert hits[0].listing_id == "l_1"
    assert hits[0].score == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_sparse_retriever_ignores_empty_query():
    eng = _FakeEngine(rows=[])
    retriever = SparseRetriever(eng)
    hits = await retriever.retrieve_sparse("   ", top_k=10)
    assert hits == []
