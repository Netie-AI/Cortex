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
    assert msgs[0]["meta"]["space"] == s1["id"]
    assert msgs[0]["meta"]["role"] == "user"
    assert msgs[0]["meta"]["name"] == "user"
    assert msgs[1]["meta"]["model"] == "m"
    assert msgs[1]["meta"]["space"] == s1["id"]
    assert msgs[1]["meta"]["role"] == "assistant"
    assert msgs[1]["meta"]["name"] == "assistant"
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
    assert got["meta"]["space"] == space["id"]
    assert got["meta"]["role"] == "agent"
    assert got["meta"]["name"] == "Scout"
    # upsert is idempotent per (space, name)
    again = store.upsert_agent(space["id"], "Scout")
    assert again["id"] == scout["id"]


def test_a2a_cursor_survives_reopen_and_does_not_rewind(tmp_path: Path) -> None:
    db = tmp_path / "crew.db"
    store = CrewStore(db)
    space = store.create_space()
    manager = store.upsert_agent(space["id"], "Manager")
    scout = store.upsert_agent(space["id"], "Scout")
    first = store.add_message(
        space["id"], "agent", "one", agent_id=manager["id"], to_agent_id=scout["id"]
    )
    second = store.add_message(
        space["id"], "agent", "two", agent_id=manager["id"], to_agent_id=scout["id"]
    )
    store.add_message(space["id"], "user", "noise")
    store.set_a2a_cursor(scout["id"], space["id"], first["seq"])
    assert store.get_a2a_cursor(scout["id"]) == first["seq"]
    inbox = store.inbox_since(space["id"], scout["id"], first["seq"])
    assert [m["id"] for m in inbox] == [second["id"]]
    store.set_a2a_cursor(scout["id"], space["id"], 0)
    assert store.get_a2a_cursor(scout["id"]) == first["seq"]
    store.close()
    again = CrewStore(db)
    assert again.get_a2a_cursor(scout["id"]) == first["seq"]
    assert [m["content"] for m in again.inbox_since(space["id"], scout["id"], first["seq"])] == [
        "two"
    ]


def test_restart_replays_inbox_newer_than_cursor(tmp_path: Path) -> None:
    from CortexOS.crew import a2a

    db = tmp_path / "crew.db"
    store = CrewStore(db)
    space = store.create_space()
    mgr = store.upsert_agent(space["id"], "Manager")
    scout = store.upsert_agent(space["id"], "Scout")
    consumed = store.add_message(
        space["id"],
        "agent",
        "already-read",
        agent_id=mgr["id"],
        to_agent_id=scout["id"],
        meta={"a2a": {"kind": a2a.TELL, "from": "Manager", "to": "Scout"}},
    )
    unread = store.add_message(
        space["id"],
        "agent",
        "UNREAD-AFTER-RESTART",
        agent_id=mgr["id"],
        to_agent_id=scout["id"],
        meta={"a2a": {"kind": a2a.TELL, "from": "Manager", "to": "Scout"}},
    )
    store.set_a2a_cursor(scout["id"], space["id"], consumed["seq"])
    names = {mgr["id"]: "Manager", scout["id"]: "Scout"}
    box = a2a.Mailbox()
    cursor = store.get_a2a_cursor(scout["id"])
    for row in store.inbox_since(space["id"], scout["id"], cursor):
        box.put(a2a.envelope_from_stored(row, names))
    kept = box.drain(after_seq=cursor)
    assert [e.text for e in kept] == ["UNREAD-AFTER-RESTART"]
    assert kept[0].seq == unread["seq"]
    assert kept[0].from_name == "Manager"


def test_stall_skips_manager_and_flags_silent_teammate(tmp_path: Path) -> None:
    from CortexOS.crew.stall import detect_stalls

    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space()
    mgr = store.upsert_agent(space["id"], "Manager")
    scout = store.upsert_agent(space["id"], "Scout")
    store.set_agent_status(mgr["id"], "thinking")
    store.set_agent_status(scout["id"], "thinking")
    old = "2020-01-01T00:00:00+00:00"
    with store._lock:
        store._db.execute(
            "UPDATE agents SET created_at = ? WHERE id IN (?, ?)",
            (old, mgr["id"], scout["id"]),
        )
        store._db.commit()
    hits = detect_stalls(store, after_s=60)
    assert [h["name"] for h in hits] == ["Scout"]
    store.add_message(space["id"], "agent", "still here", agent_id=scout["id"])
    assert detect_stalls(store, after_s=60) == []


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


def test_an_existing_database_gains_the_approval_columns(tmp_path: Path) -> None:
    """An operator's crew.db predates the approval arms; opening it must migrate.

    Without the ALTER TABLE the first spawn carrying reject_tools raises
    OperationalError inside a run, which surfaces as "Run crashed" and reads as
    a model failure rather than a schema one.
    """
    import sqlite3

    db_path = tmp_path / "old.db"
    old = sqlite3.connect(str(db_path))
    old.executescript(
        "CREATE TABLE agents (id TEXT PRIMARY KEY, space_id TEXT NOT NULL,"
        " name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '',"
        " color TEXT NOT NULL DEFAULT '', role_prompt TEXT NOT NULL DEFAULT '',"
        " status TEXT NOT NULL DEFAULT 'idle', spawned_by TEXT,"
        " created_at TEXT NOT NULL, UNIQUE (space_id, name));"
    )
    old.commit()
    old.close()

    store = CrewStore(db_path)
    space = store.create_space("Legacy")
    agent = store.upsert_agent(space["id"], "Scout", reject_tools=["cortex_ask"])
    assert agent["reject_tools"] == '["cortex_ask"]'
    assert agent["approve_tools"] == ""
    assert agent["worktree_path"] == ""
    store.close()


def test_worktree_skips_without_root(tmp_path: Path, monkeypatch) -> None:
    from CortexOS.crew.worktree import attach_worktree

    monkeypatch.delenv("CREW_WORKTREE_ROOT", raising=False)
    monkeypatch.delenv("CREW_WORKTREE_FROM", raising=False)
    out = attach_worktree("Scout", tmp_path)
    assert out["ok"] is False
    assert "unset" in out["error"]


def test_worktree_attach_and_store_path(tmp_path: Path) -> None:
    import subprocess

    from CortexOS.crew.worktree import attach_worktree

    repo = tmp_path / "repo"
    repo.mkdir()
    dest_parent = tmp_path / "data"
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "readme.txt").write_text("ok", encoding="utf-8")
    subprocess.run(["git", "add", "readme.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=crew", "-c", "user.email=crew@local", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    wt = attach_worktree("Scout", dest_parent, source=repo)
    assert wt["ok"] is True, wt
    assert (Path(wt["path"]) / "readme.txt").is_file()
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space()
    scout = store.upsert_agent(space["id"], "Scout")
    store.set_worktree_path(scout["id"], wt["path"])
    assert store.get_agent(scout["id"])["worktree_path"] == wt["path"]
