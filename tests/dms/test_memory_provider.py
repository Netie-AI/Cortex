"""A4 — persistent raw KNN, semantic cache, and unified context assembly."""

from __future__ import annotations

from CortexOS.memory.context_provider import MemoryContextProvider
from CortexOS.memory.semantic_cache import SemanticCache
from CortexOS.memory.store import InMemoryStore, MemoryRecord
from CortexOS.memory.stores.rawknn import RawKnnStore


def test_rawknn_persists_and_queries_across_instances(tmp_path):
    root = tmp_path / "rawknn"
    first = RawKnnStore(root, dim=2)
    assert first.upsert([MemoryRecord(id="first", text="persisted", vector=[1.0, 0.0])]) == 1
    first.close()

    second = RawKnnStore(root, dim=2)
    try:
        hits = second.query([1.0, 0.0])
        assert [hit.id for hit in hits] == ["first"]
    finally:
        second.close()


def test_semantic_cache_returns_cached_answer_on_second_lookup():
    cache = SemanticCache()
    answer = {"answer": "already computed"}

    assert cache.get([1.0, 0.0]) is None
    cache.put([1.0, 0.0], answer)

    assert cache.get([1.0, 0.0]) is answer
    assert cache.hit_count == 1


def test_memory_context_provider_returns_vector_hits_and_optional_context():
    store = InMemoryStore()
    store.upsert([MemoryRecord(id="memory", text="relevant fact", vector=[1.0, 0.0])])
    provider = MemoryContextProvider(
        store,
        notes_loader=lambda: ["saved note"],
        chat_loader=lambda session_id: [{"content": f"chat:{session_id}"}],
    )

    context = provider.assemble([1.0, 0.0], session_id="s-1")

    assert [hit.id for hit in context["vector_hits"]] == ["memory"]
    assert context["chat_turns"] == [{"content": "chat:s-1"}]
    assert context["notes"] == ["saved note"]
    assert context["text_blob"] == "relevant fact\nchat:s-1\nsaved note"
    assert context["layers"]["session"] == context["chat_turns"]
    assert context["layers"]["long_term"] == context["notes"]
    assert [hit.id for hit in context["layers"]["vector"]] == ["memory"]
