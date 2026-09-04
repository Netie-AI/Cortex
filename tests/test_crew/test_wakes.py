"""Crew wake ticks. Not a second mailbox. Control never POSTs."""

from __future__ import annotations

from CortexOS.crew.a2a import Mailbox
from CortexOS.crew.queue import JobQueue
from CortexOS.crew.store import CrewStore
from CortexOS.crew.wakes import WakeBoard, conveyor


def test_arm_lists_kind_state_note() -> None:
    board = WakeBoard()
    row = board.arm("timer", "morning brief")
    assert row["kind"] == "timer"
    assert row["state"] == "pending"
    assert row["note"] == "morning brief"
    public = board.public()
    assert public == [{"kind": "timer", "state": "pending", "note": "morning brief"}]
    snap = board.snapshot()
    assert snap["ok"] is True
    assert snap["wakes"] == public


def test_mailbox_cursor_still_lives_on_a2a_not_wakeboard() -> None:
    """drain(after_seq) is the mailbox cursor. WakeBoard must not replace it."""
    box = Mailbox()
    from CortexOS.crew.a2a import Envelope

    box.put(
        Envelope(
            id="e1",
            kind="tell",
            from_id="a",
            from_name="A",
            to_id="b",
            to_name="B",
            text="old",
            seq=3,
        )
    )
    box.put(
        Envelope(
            id="e2",
            kind="tell",
            from_id="a",
            from_name="A",
            to_id="b",
            to_name="B",
            text="new",
            seq=5,
        )
    )
    kept = box.drain(after_seq=4)
    assert [env.text for env in kept] == ["new"]
    assert WakeBoard().list() == []


def test_conveyor_skips_cortex_ping_and_does_not_decide_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CREW_CLAIMS", str(tmp_path / "missing-claims.json"))
    monkeypatch.setenv("CREW_RUNTIME", str(tmp_path / "missing-runtime.md"))
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space("HQ")
    store.upsert_agent(space["id"], "Scout")
    wakes = WakeBoard()
    wakes.arm("timer", "morning brief")
    queue = JobQueue()
    body = conveyor(store, wakes, queue, mailbox_nonempty=True)
    assert body["bus"] == "github-issues"
    assert body["converse"] is True
    assert body["cortex"] == {"ok": False, "detail": "not probed"}
    assert body["plan_for_next"]["decides_work_shape"] is False
    assert body["plan_for_next"]["needs_human"] is True
    assert body["wakes"][0]["note"] == "morning brief"
    assert any(w["kind"] == "mailbox" for w in body["wakes"])
    assert body["queue"] == {"pending": 0, "leased": 0, "done": 0, "dead": 0}
    assert body["spaces"][0]["id"] == space["id"]
    assert body["agents"][0]["name"] == "Scout"
    assert body["assignments"] == []
    assert "Control does not assign" in body["assign_owner"]
    assert "dag_runner" not in body
