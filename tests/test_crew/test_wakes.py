"""Timer/completion/event wakes. Crew owns the tick. Control never POSTs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from CortexOS.crew.llm import LLMResult
from CortexOS.crew.runtime import RunContext
from CortexOS.crew.server import create_app
from CortexOS.crew.wakes import (
    KIND_EVENT,
    KIND_TIMER,
    STATE_DUE,
    STATE_FIRED,
    WakeStore,
    expand_wake_note,
    is_semantic_layer_wake,
)
from tests.test_crew.conftest import FakeLLM, wait_run_done


def test_timer_wake_becomes_due(tmp_path: Path) -> None:
    store = WakeStore(tmp_path / "wakes.json")
    past = datetime.now(timezone.utc) - timedelta(seconds=2)
    w = store.add_timer("space-a", past, note="call me")
    due = store.due()
    assert due[0].id == w.id
    assert due[0].kind == KIND_TIMER
    fired = store.mark_fired(w.id)
    assert fired is not None
    assert fired.state == STATE_FIRED
    assert store.due() == []
    again = WakeStore(tmp_path / "wakes.json")
    assert again.list(include_fired=True)[0].state == STATE_FIRED


def test_catalog_shorthand_expands_to_cortex_ask_catalog() -> None:
    from CortexOS.crew.wakes import SEMANTIC_LAYER_WAKE_NOTE

    assert expand_wake_note("catalog") == SEMANTIC_LAYER_WAKE_NOTE
    assert expand_wake_note("24/7") == SEMANTIC_LAYER_WAKE_NOTE
    assert "cortex_ask" in expand_wake_note("insight automation")
    assert expand_wake_note("call me") == "call me"
    assert is_semantic_layer_wake("catalog")
    assert is_semantic_layer_wake("[wake] " + SEMANTIC_LAYER_WAKE_NOTE)
    assert not is_semantic_layer_wake("call me")


def test_completion_wake_marks_due(tmp_path: Path) -> None:
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_completion("space-a", "job-9", note="when PR lands")
    assert store.due() == []
    due = store.complete_job("job-9")
    assert due[0].id == w.id
    assert [x.id for x in store.due()] == [w.id]
    assert due[0].state == STATE_DUE


def test_event_wake_marks_due(tmp_path: Path) -> None:
    store = WakeStore(tmp_path / "wakes.json")
    w = store.add_event("space-a", "ship-gate", note="when gate greens")
    assert store.due() == []
    due = store.fire_event("ship-gate")
    assert due[0].id == w.id
    assert due[0].kind == KIND_EVENT


def test_finish_run_marks_completion_wake_due(rig, tmp_path: Path) -> None:
    store = WakeStore(tmp_path / "wakes.json")
    rig.runtime.wakes = store
    store.add_completion("space-a", "run-9", note="after this run")
    ctx = RunContext(id="run-9", space_id="space-a")
    rig.runtime._finish_run_lease(ctx, "done")
    due = store.due()
    assert due and due[0].job_id == "run-9"
    assert due[0].state == STATE_DUE


@pytest.mark.asyncio
async def test_due_timer_fires_into_transcript(settings, crew_env) -> None:
    fake = FakeLLM()
    fake.manager.append(LLMResult(text="I am awake."))
    app = create_app(settings, llm_chat=fake)
    crew = app.state.crew
    await crew.startup()
    try:
        space = crew.store.create_space("Wake")
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        crew.wakes.add_timer(space["id"], past, note="call back")
        fired = await crew.fire_due_wakes()
        assert fired and fired[0]["note"] == "call back"
        await wait_run_done(crew.runtime, space["id"])
        texts = [m["content"] for m in crew.store.list_messages(space["id"])]
        assert any("[wake]" in t and "call back" in t for t in texts)
    finally:
        await crew.shutdown()


@pytest.mark.asyncio
async def test_catalog_wake_posts_engine_catalog_without_llm(settings, crew_env, monkeypatch) -> None:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    crew = app.state.crew

    async def catalog_ask(question: str) -> dict:
        assert "metrics" in question
        return {
            "ok": True,
            "answer": "Here is what you can ask from the DMS semantic layer:",
            "badge": "catalog",
        }

    monkeypatch.setattr(crew.runtime.bridge, "ask", catalog_ask)
    await crew.startup()
    try:
        space = crew.store.create_space("Catalog")
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        crew.wakes.add_timer(space["id"], past, note=expand_wake_note("catalog"))
        fired = await crew.fire_due_wakes()
        assert fired and "metrics" in fired[0]["note"]
        texts = [m["content"] for m in crew.store.list_messages(space["id"])]
        assert any("semantic layer" in t for t in texts)
        assert any("badge: catalog" in t for t in texts)
        assert fake.manager == []
    finally:
        await crew.shutdown()


def test_hitl_decide_fires_event_wake(rig, tmp_path: Path) -> None:
    store = WakeStore(tmp_path / "wakes.json")
    rig.runtime.wakes = store
    space = rig.store.create_space("Hitl")
    store.add_event(space["id"], "hitl", note="after confirm")
    confirm = rig.store.create_confirm(
        space["id"], run_id=None, agent_id=None, tool="win.Type", args={"text": "hi"}
    )
    assert store.due() == []
    row = rig.runtime.decide_confirm(confirm["id"], True)
    assert row is not None and row["status"] == "approved"
    due = store.due()
    assert due and due[0].event_key == "hitl"


def test_stall_cut_fires_event_wake(settings, crew_env) -> None:
    """Seq-age cut is a named event. Control never POSTs it. Manager stays."""
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    crew = app.state.crew
    space = crew.store.create_space("Stall")
    scout = crew.store.upsert_agent(space["id"], "Scout")
    crew.store.set_agent_status(scout["id"], "thinking")
    old = "2020-01-01T00:00:00+00:00"
    with crew.store._lock:
        crew.store._db.execute(
            "UPDATE agents SET created_at = ? WHERE id = ?",
            (old, scout["id"]),
        )
        crew.store._db.commit()
    crew.wakes.add_event(space["id"], "stall", note="after cut")
    assert crew.wakes.due() == []
    cut = crew.fire_stalls()
    assert cut and cut[0]["name"] == "Scout"
    due = crew.wakes.due()
    assert due and due[0].event_key == "stall"
    assert all(h.get("name") != "Manager" for h in cut)
    crew.store.close()


def test_stranded_run_fires_event_wake(rig, tmp_path: Path) -> None:
    """Restart names the in-flight run and fires stranded. It does not autorun."""
    store = WakeStore(tmp_path / "wakes.json")
    rig.runtime.wakes = store
    space = rig.store.create_space("HQ")
    store.add_event(space["id"], "stranded", note="after restart")
    item = rig.runtime.work_queue.push(
        space["id"], {"kind": "run", "run_id": "run-dead"}, item_id="run-dead"
    )
    rig.runtime.work_queue.claim_item(item.id, rig.runtime._queue_worker)
    assert store.due() == []
    notes = rig.runtime.recover_stranded()
    assert notes and notes[0]["item_id"] == item.id
    due = store.due()
    assert due and due[0].event_key == "stranded"
    assert rig.runtime._space_run.get(space["id"]) is None


def test_cancel_run_fires_event_wake(settings, crew_env) -> None:
    """Operator cancel is a named event. Control never POSTs it."""
    from types import SimpleNamespace

    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    crew = app.state.crew
    space = crew.store.create_space("Cancel")
    crew.wakes.add_event(space["id"], "cancel", note="after cancel")
    crew.runtime._runs["run-x"] = SimpleNamespace(space_id=space["id"], tasks=[])
    assert crew.wakes.due() == []
    assert crew.runtime.cancel_run("run-x") is True
    due = crew.wakes.due()
    assert due and due[0].event_key == "cancel"
    assert crew.runtime.cancel_run("no-such") is False
    crew.store.close()
