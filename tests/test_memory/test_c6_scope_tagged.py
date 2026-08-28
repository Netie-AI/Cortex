"""C6 — scope-tagged memory: entry_scope ⊆ session_scope in the storage query."""

from __future__ import annotations

from CortexOS.dms import answer_engine
from CortexOS.memory.scope import (
    normalize_scope,
    scope_subseteq,
    session_scope_from,
    space_tag,
    sql_entry_subseteq_session,
)
from CortexOS.memory.store import InMemoryStore, MemoryRecord
from CortexOS.memory.stores.rawknn import RawKnnStore


def test_scope_subseteq_semantics():
    assert scope_subseteq({"space:a"}, {"space:a", "space:b", "tenant:t"})
    assert not scope_subseteq({"space:a", "space:b"}, {"space:a"})
    assert not scope_subseteq({"space:a"}, set())
    # Math: ∅ ⊆ S. Stores still refuse empty entry_scope on the session_scope path.
    assert scope_subseteq(set(), {"space:a"})
    assert normalize_scope([" space:a ", "", "space:a"]) == frozenset({"space:a"})


def test_sql_subset_clause_fail_closed_on_empty_session():
    clause, args = sql_entry_subseteq_session(session_tags=frozenset())
    assert clause == "0"
    assert args == []


def test_inmemory_session_scope_filters_before_rank():
    store = InMemoryStore()
    store.upsert(
        [
            MemoryRecord(
                id="a",
                text="space-a secret",
                vector=[1.0, 0.0],
                entry_scope=frozenset({"tenant:t", "space:a"}),
            ),
            MemoryRecord(
                id="b",
                text="space-b secret",
                vector=[0.99, 0.01],  # nearer to query than chance alone
                entry_scope=frozenset({"tenant:t", "space:b"}),
            ),
            MemoryRecord(
                id="wider",
                text="needs both spaces",
                vector=[1.0, 0.0],
                entry_scope=frozenset({"tenant:t", "space:a", "space:b"}),
            ),
        ]
    )
    sess_a = session_scope_from(space_id="a", tenant_id="t")
    hits = store.query([1.0, 0.0], k=5, session_scope=sess_a)
    ids = [h.id for h in hits]
    assert "a" in ids
    assert "b" not in ids
    assert "wider" not in ids  # entry has space:b not in session


def test_inmemory_empty_session_scope_returns_nothing():
    store = InMemoryStore()
    store.upsert(
        [MemoryRecord(id="x", text="x", vector=[1.0, 0.0], entry_scope=frozenset({"space:a"}))]
    )
    assert store.query([1.0, 0.0], session_scope=frozenset()) == []


def test_rawknn_subset_in_sql(tmp_path):
    root = tmp_path / "rawknn-c6"
    store = RawKnnStore(root, dim=2)
    try:
        store.upsert(
            [
                MemoryRecord(
                    id="a",
                    text="a",
                    vector=[1.0, 0.0],
                    entry_scope=frozenset({"tenant:t", "space:a"}),
                ),
                MemoryRecord(
                    id="b",
                    text="b",
                    vector=[1.0, 0.0],
                    entry_scope=frozenset({"tenant:t", "space:b"}),
                ),
            ]
        )
        # Prove filter is SQL-side: candidate SELECT must not return b for space:a session.
        sess = frozenset({"tenant:t", "space:a"})
        clause, args = sql_entry_subseteq_session(session_tags=sess)
        rows = store._db.execute(
            f"SELECT id FROM records r WHERE {clause}", args
        ).fetchall()
        assert [r[0] for r in rows] == ["a"]

        hits = store.query([1.0, 0.0], k=5, session_scope=sess)
        assert [h.id for h in hits] == ["a"]
    finally:
        store.close()


def test_session_anaphora_isolated_by_space():
    """Same session_id, different space_id must not share prior SQL (C6)."""
    answer_engine.clear_session()
    answer_engine._remember(
        "sess-1",
        {
            "sql": "SELECT 100 AS revenue_myr",
            "rows": [{"revenue_myr": 100}],
            "total_count": 1,
            "metric_id": "revenue_ytd",
            "layer": "governed_metric",
        },
        space_id="space-a",
    )

    prior_b = answer_engine._SESSION.get(answer_engine._session_key("sess-1", "space-b"))
    assert prior_b is None
    prior_a = answer_engine._SESSION.get(answer_engine._session_key("sess-1", "space-a"))
    assert prior_a is not None
    assert prior_a["sql"].startswith("SELECT 100")
    assert space_tag("space-a") == "space:space-a"
