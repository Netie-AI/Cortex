"""Runtime parks in-flight runs on the durable queue. Restart names them."""

from __future__ import annotations

import pytest

from CortexOS.crew.llm import LLMResult
from CortexOS.crew.queue import LEASED, PENDING
from tests.test_crew.conftest import wait_run_done


@pytest.mark.asyncio
async def test_a_finished_run_is_marked_done_on_disk(rig) -> None:
    rig.llm.manager.append(LLMResult(text="pong"))
    space = rig.store.create_space("HQ")
    posted = await rig.runtime.on_user_message(space["id"], "reply pong")
    await wait_run_done(rig.runtime, space["id"])
    row = rig.runtime.work_queue.get(posted["run_id"])
    assert row is not None
    assert row.status == "done"
    assert row.payload["kind"] == "run"


@pytest.mark.asyncio
async def test_restart_names_a_stranded_run_and_does_not_autorun(rig) -> None:
    space = rig.store.create_space("HQ")
    item = rig.runtime.work_queue.push(
        space["id"], {"kind": "run", "run_id": "run-dead"}, item_id="run-dead"
    )
    rig.runtime.work_queue.claim_item(item.id, rig.runtime._queue_worker)
    assert rig.runtime.work_queue.get(item.id).status == LEASED  # type: ignore[union-attr]

    notes = rig.runtime.recover_stranded()
    assert notes and notes[0]["item_id"] == item.id
    back = rig.runtime.work_queue.get(item.id)
    assert back is not None
    assert back.status == PENDING
    assert rig.runtime._space_run.get(space["id"]) is None
    texts = [m["content"] for m in rig.store.list_messages(space["id"])]
    assert any("Crew restarted" in t and "run-dead" in t for t in texts)


@pytest.mark.asyncio
async def test_queued_user_turn_resumes_after_restart(rig) -> None:
    space = rig.store.create_space("HQ")
    follow = rig.store.add_message(space["id"], "user", "what is left?")
    rig.runtime.work_queue.push(
        space["id"],
        {"kind": "user", "text": "what is left?", "message_id": follow["id"]},
        item_id=follow["id"],
    )
    rig.llm.manager.append(LLMResult(text="the leftover work"))
    rig.runtime.recover_stranded()
    await wait_run_done(rig.runtime, space["id"])
    answers = [m["content"] for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"]
    assert answers and "leftover" in answers[-1]
    row = rig.runtime.work_queue.get(follow["id"])
    assert row is not None
    assert row.status == "done"
