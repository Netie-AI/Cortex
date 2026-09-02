"""Durable queue tests - assert on what an operator would see afterwards.

Every case here checks the stored item (status, attempts, last_reason), not an
internal call count: a queue that reported a retry it never recorded would look
green while a crash loop stayed invisible.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from CortexOS.crew.queue import (
    DEAD,
    DONE,
    LEASED,
    PENDING,
    DurableQueue,
    LeaseLost,
    QueueCorrupt,
    QueueError,
    QueueItem,
    UnknownItem,
    queue_for,
)


class FakeClock:
    """Wall clock under test control - lease expiry without sleeping."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def q(tmp_path: Path, clock: FakeClock) -> DurableQueue:
    return DurableQueue(
        tmp_path / "queue" / "work.jsonl",
        lease_seconds=30.0,
        max_attempts=3,
        clock=clock,
    )


def test_push_then_claim_survives_a_restart(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "queue" / "work.jsonl"
    first = DurableQueue(path, clock=clock)
    pushed = first.push("space-1", {"kind": "ask", "text": "revenue?"})

    # A brand new object over the same file stands in for a restarted process:
    # nothing is carried over in memory.
    second = DurableQueue(path, clock=clock)
    claimed = second.claim("worker-a")

    assert claimed is not None
    assert claimed.id == pushed.id
    assert claimed.payload == {"kind": "ask", "text": "revenue?"}
    assert claimed.status == LEASED
    assert claimed.lease_owner == "worker-a"
    assert second.get(pushed.id) is not None
    assert second.get(pushed.id).status == LEASED  # type: ignore[union-attr]


def test_claim_on_an_empty_queue_returns_none(q: DurableQueue) -> None:
    assert q.claim("worker-a") is None


def test_claim_is_fifo_and_can_filter_by_space(q: DurableQueue) -> None:
    a = q.push("space-1", {"n": 1})
    b = q.push("space-2", {"n": 2})
    c = q.push("space-1", {"n": 3})

    assert q.claim("w1", space_id="space-1").id == a.id  # type: ignore[union-attr]
    assert q.claim("w2", space_id="space-1").id == c.id  # type: ignore[union-attr]
    assert q.claim("w3", space_id="space-1") is None
    assert q.claim("w4").id == b.id  # type: ignore[union-attr]


def test_expired_lease_is_reclaimable_and_the_attempt_is_visible(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-dead")

    clock.advance(31.0)
    reaped = q.reap()

    assert [r.id for r in reaped] == [item.id]
    back = q.get(item.id)
    assert back is not None
    assert back.status == PENDING
    assert back.attempts == 1, "a silent retry would hide the crash loop"
    assert "expired" in back.last_reason
    assert "worker-dead" in back.last_reason

    again = q.claim("worker-b")
    assert again is not None
    assert again.id == item.id
    assert again.attempts == 1


def test_claim_reclaims_an_expired_lease_without_an_explicit_reap(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-dead")
    clock.advance(31.0)

    taken = q.claim("worker-b")

    assert taken is not None
    assert taken.id == item.id
    assert taken.lease_owner == "worker-b"
    assert taken.attempts == 1


def test_a_live_lease_is_never_handed_out_twice(q: DurableQueue, clock: FakeClock) -> None:
    q.push("space-1", {"n": 1})
    assert q.claim("worker-a") is not None

    clock.advance(29.0)  # still inside the 30s lease
    assert q.claim("worker-b") is None
    assert q.reap() == []


def test_heartbeat_extends_the_lease(q: DurableQueue, clock: FakeClock) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-a")

    clock.advance(20.0)
    q.heartbeat(item.id, "worker-a")
    clock.advance(20.0)  # past the original expiry, inside the extended one

    assert q.reap() == []
    assert q.claim("worker-b") is None
    held = q.get(item.id)
    assert held is not None and held.status == LEASED and held.attempts == 0


def test_heartbeat_after_a_reclaim_is_refused_with_a_reason(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-dead")
    clock.advance(31.0)
    q.claim("worker-b")

    with pytest.raises(LeaseLost) as excinfo:
        q.heartbeat(item.id, "worker-dead")

    assert "worker-b" in str(excinfo.value)
    assert item.id in str(excinfo.value)


def test_complete_marks_done_and_it_is_not_handed_out_again(q: DurableQueue) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-a")

    done = q.complete(item.id, "worker-a")

    assert done.status == DONE
    assert done.lease_owner == ""
    assert q.claim("worker-b") is None
    assert q.counts() == {PENDING: 0, LEASED: 0, DONE: 1, DEAD: 0}


def test_complete_from_a_worker_that_lost_the_lease_is_refused(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-dead")
    clock.advance(31.0)
    q.reap()

    with pytest.raises(LeaseLost) as excinfo:
        q.complete(item.id, "worker-dead")

    assert "pending" in str(excinfo.value)
    assert q.get(item.id).status == PENDING  # type: ignore[union-attr]


def test_fail_records_the_reason_and_returns_the_item_to_pending(q: DurableQueue) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-a")

    failed = q.fail(item.id, "worker-a", "engine offline")

    assert failed.status == PENDING
    assert failed.attempts == 1
    assert "engine offline" in failed.last_reason
    assert q.claim("worker-b") is not None


def test_an_item_goes_dead_with_its_last_reason_and_is_never_dropped(
    q: DurableQueue,
) -> None:
    item = q.push("space-1", {"n": 1})
    for attempt in range(3):
        q.claim(f"worker-{attempt}")
        q.fail(item.id, f"worker-{attempt}", f"boom {attempt}")

    dead = q.get(item.id)
    assert dead is not None
    assert dead.status == DEAD
    assert dead.attempts == 3
    assert "boom 2" in dead.last_reason
    assert "no attempts left" in dead.last_reason
    assert q.claim("worker-x") is None
    assert [i.id for i in q.list_items(status=DEAD)] == [item.id]
    assert q.counts()[DEAD] == 1


def test_repeated_lease_expiry_also_ends_in_dead_not_in_an_endless_retry(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"n": 1}, max_attempts=2)

    for worker in ("worker-a", "worker-b"):
        assert q.claim(worker) is not None
        clock.advance(31.0)
        q.reap()

    dead = q.get(item.id)
    assert dead is not None
    assert dead.status == DEAD
    assert dead.attempts == 2
    assert "expired" in dead.last_reason and "no attempts left" in dead.last_reason


def test_fail_does_not_double_count_an_attempt_already_reaped(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"n": 1})
    q.claim("worker-dead")
    clock.advance(31.0)
    q.reap()

    with pytest.raises(LeaseLost):
        q.fail(item.id, "worker-dead", "late failure report")

    assert q.get(item.id).attempts == 1  # type: ignore[union-attr]


def test_two_threads_never_claim_the_same_item(tmp_path: Path) -> None:
    q = DurableQueue(tmp_path / "work.jsonl", lease_seconds=300.0)
    pushed = {q.push("space-1", {"n": n}).id for n in range(12)}

    with ThreadPoolExecutor(max_workers=12) as pool:
        claimed = list(pool.map(lambda n: q.claim(f"worker-{n}"), range(24)))

    ids = [item.id for item in claimed if item is not None]
    assert len(ids) == 12
    assert set(ids) == pushed
    assert len(set(ids)) == len(ids), "one item was handed to two workers"
    assert q.counts()[LEASED] == 12


def test_two_queue_objects_over_one_file_never_hand_out_the_same_item(
    tmp_path: Path,
) -> None:
    path = tmp_path / "work.jsonl"
    a = DurableQueue(path, lease_seconds=300.0)
    b = DurableQueue(path, lease_seconds=300.0)
    a.push("space-1", {"n": 1})

    with ThreadPoolExecutor(max_workers=2) as pool:
        got = [f.result() for f in (pool.submit(a.claim, "a"), pool.submit(b.claim, "b"))]

    winners = [item for item in got if item is not None]
    assert len(winners) == 1
    assert b.get(winners[0].id).lease_owner == winners[0].lease_owner  # type: ignore[union-attr]


def test_a_corrupt_line_is_refused_loudly_rather_than_skipped(tmp_path: Path) -> None:
    path = tmp_path / "work.jsonl"
    q = DurableQueue(path)
    q.push("space-1", {"n": 1})
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(QueueCorrupt) as excinfo:
        q.claim("worker-a")

    assert "line 2" in str(excinfo.value)


def test_a_line_without_an_id_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "work.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"space_id": "s", "status": PENDING}) + "\n", encoding="utf-8")

    with pytest.raises(QueueCorrupt):
        DurableQueue(path).list_items()


def test_a_payload_that_cannot_be_stored_is_refused_before_it_is_queued(
    q: DurableQueue,
) -> None:
    with pytest.raises(QueueError) as excinfo:
        q.push("space-1", {"handle": object()})

    assert "JSON" in str(excinfo.value)
    assert q.list_items() == []


def test_duplicate_ids_are_refused(q: DurableQueue) -> None:
    q.push("space-1", {"n": 1}, item_id="fixed")
    with pytest.raises(QueueError):
        q.push("space-1", {"n": 2}, item_id="fixed")
    assert len(q.list_items()) == 1


def test_operations_on_an_unknown_item_say_so(q: DurableQueue) -> None:
    with pytest.raises(UnknownItem):
        q.heartbeat("nope", "worker-a")
    with pytest.raises(UnknownItem):
        q.complete("nope", "worker-a")
    with pytest.raises(UnknownItem):
        q.fail("nope", "worker-a", "x")


def test_stored_rows_round_trip_through_json(q: DurableQueue) -> None:
    item = q.push("space-1", {"kind": "ask"})
    row = json.loads(q.path.read_text(encoding="utf-8").splitlines()[0])
    assert QueueItem.from_dict(row) == item


def test_queue_for_lives_beside_the_other_crew_data(tmp_path: Path) -> None:
    made = queue_for(tmp_path / "crew")
    assert made.path == tmp_path / "crew" / "queue" / "work.jsonl"
    assert made.path.parent.is_dir()


def test_claim_item_leases_only_that_row(q: DurableQueue) -> None:
    first = q.push("space-1", {"n": 1})
    second = q.push("space-1", {"n": 2})
    claimed = q.claim_item(second.id, "worker-b")
    assert claimed.id == second.id
    assert claimed.status == LEASED
    assert q.get(first.id).status == PENDING  # type: ignore[union-attr]


def test_abandon_leases_on_restart_does_not_wait_for_ttl(
    q: DurableQueue, clock: FakeClock
) -> None:
    item = q.push("space-1", {"kind": "run", "run_id": "r1"})
    q.claim("worker-dead")
    clock.advance(1.0)
    moved = q.abandon_leases("process_restart")
    assert [m.id for m in moved] == [item.id]
    back = q.get(item.id)
    assert back is not None
    assert back.status == PENDING
    assert back.attempts == 1
    assert "process_restart" in back.last_reason
