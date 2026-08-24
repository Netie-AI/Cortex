from __future__ import annotations

from pathlib import Path

from CortexOS.crew.store import CrewStore


def test_spaces_are_ordered_and_archivable(tmp_path: Path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    a = store.create_space("First")
    b = store.create_space("Second")
    assert [s["title"] for s in store.list_spaces()] == ["First", "Second"]

    store.update_space(a["id"], ord=b["ord"])
    store.update_space(b["id"], ord=a["ord"])
    assert [s["title"] for s in store.list_spaces()] == ["Second", "First"]

    store.archive_space(a["id"])
    assert [s["title"] for s in store.list_spaces()] == ["Second"]


def test_message_seq_is_a_per_space_total_order(tmp_path: Path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    s1 = store.create_space("One")
    s2 = store.create_space("Two")
    store.add_message(s1["id"], "user", "hi")
    store.add_message(s2["id"], "user", "yo")
    m3 = store.add_message(s1["id"], "assistant", "hello", meta={"model": "m"})
    assert m3["seq"] == 2
    msgs = store.list_messages(s1["id"])
    assert [m["seq"] for m in msgs] == [1, 2]
    assert msgs[1]["meta"] == {"model": "m"}
    assert store.list_messages(s1["id"], after=1)[0]["id"] == m3["id"]


def test_a2a_fields_roundtrip(tmp_path: Path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space()
    manager = store.upsert_agent(space["id"], "Manager", color="#4e6b16")
    scout = store.upsert_agent(space["id"], "Scout", spawned_by=manager["id"])
    msg = store.add_message(
        space["id"], "agent", "found it", agent_id=scout["id"], to_agent_id=manager["id"]
    )
    got = store.list_messages(space["id"])[0]
    assert (got["agent_id"], got["to_agent_id"]) == (scout["id"], manager["id"])
    assert msg["role"] == "agent"
    # upsert is idempotent per (space, name)
    again = store.upsert_agent(space["id"], "Scout")
    assert again["id"] == scout["id"]


def test_rename_and_grants_roundtrip(tmp_path: Path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space()
    store.upsert_agent(space["id"], "Scout", deny_tools=["click"], capability="PRD")
    renamed = store.rename_agent(space["id"], "Scout", "sku-scout")
    assert renamed is not None and renamed["name"] == "sku-scout"
    assert "click" in (renamed.get("deny_tools") or "")
    assert store.rename_agent(space["id"], "missing", "x") is None
    clash = store.upsert_agent(space["id"], "Other")
    assert store.rename_agent(space["id"], "sku-scout", clash["name"]) is None


def test_confirm_lifecycle(tmp_path: Path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space()
    c = store.create_confirm(
        space["id"], run_id=None, agent_id=None, tool="win.Type", args={"text": "hi"}
    )
    assert store.pending_confirms(space["id"])[0]["id"] == c["id"]
    decided = store.decide_confirm(c["id"], approved=True)
    assert decided is not None and decided["status"] == "approved"
    assert store.pending_confirms(space["id"]) == []
    # a decided confirm cannot flip
    again = store.decide_confirm(c["id"], approved=False)
    assert again is not None and again["status"] == "approved"

    wall = store.create_confirm(
        space["id"], run_id=None, agent_id=None, tool="uacc.type_text", args={"password": "x"}
    )
    took = store.decide_confirm(wall["id"], approved=False, takeover=True)
    assert took is not None and took["status"] == "takeover"
    assert store.pending_confirms(space["id"]) == []
