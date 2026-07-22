from dataclasses import dataclass

import pytest

from netie.rag.retriever_dense import DenseRetriever, stable_point_uuid


@pytest.fixture(autouse=True)
def _stub_qdrant_models(monkeypatch):
    """DenseRetriever imports real Qdrant model types lazily — stub them for offline tests."""

    class Distance:
        COSINE = object()

    @dataclass
    class PointStruct:
        id: str | None = None
        vector: list[float] | None = None
        payload: dict | None = None

    @dataclass
    class VectorParams:
        size: int
        distance: object

    def fake_models():
        return Distance, PointStruct, VectorParams

    monkeypatch.setattr("netie.rag.retriever_dense._qdrant_models", fake_models)


class _StubEmbedder:
    dimension = 8

    def encode_dense(self, text: str, *, normalize_embeddings: bool = True) -> list[float]:  # noqa: ARG002
        base = hash(text) % 997 / 997.0
        return [base + i * 0.01 for i in range(self.dimension)]


class _Named:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCollections:
    def __init__(self, names: list[str]) -> None:
        self.collections = [_Named(n) for n in names]


class _FakeAsyncQdrant:
    """Minimal async substitute — no dependency on installed qdrant-client."""

    def __init__(self, *_a, **_kw) -> None:
        self._collection_names: list[str] = []
        self.upserts: list[dict] = []
        self.searches: list[dict] = []
        self.created: list[dict] = []

    async def get_collections(self):
        return _FakeCollections(list(self._collection_names))

    async def create_collection(self, **kw: object) -> None:
        name = kw.get("collection_name")
        assert isinstance(name, str)
        self.created.append(dict(kw))
        self._collection_names.append(name)

    async def upsert(self, **kw: object) -> None:
        self.upserts.append(dict(kw))

    async def search(self, **kw: object) -> list:
        self.searches.append(dict(kw))
        return []


def test_stable_point_uuid_is_deterministic():
    assert stable_point_uuid("L-001") == stable_point_uuid("L-001")
    assert stable_point_uuid("L-001") != stable_point_uuid("L-002")


@pytest.mark.asyncio
async def test_upsert_creates_collection_then_inserts():
    fake = _FakeAsyncQdrant()
    retriever = DenseRetriever(
        qdrant_url="http://127.0.0.1:6333",
        collection_name="listings_dense_v1",
        embedder=_StubEmbedder(),
        client=fake,
    )

    await retriever.upsert_document(listing_id="L-99", text="condo KL", payload={"district": "KL"})

    assert fake.created, "collection should have been ensured"
    assert fake.upserts
    pts = fake.upserts[-1]["points"]
    assert len(pts) == 1


@pytest.mark.asyncio
async def test_retrieve_empty_when_collection_missing():
    fake = _FakeAsyncQdrant()
    retriever = DenseRetriever(
        qdrant_url="http://127.0.0.1:6333",
        collection_name="missing",
        embedder=_StubEmbedder(),
        client=fake,
    )
    out = await retriever.retrieve_dense_query("mid valley rent", top_k=10)
    assert out == []
