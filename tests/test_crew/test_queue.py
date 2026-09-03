"""Crew lease queue. Not a second mailbox and not LangGraph."""

from __future__ import annotations

from CortexOS.crew.a2a import Mailbox
from CortexOS.crew.queue import JobQueue


def test_enqueue_lease_ack_counts() -> None:
    q = JobQueue()
    job = q.enqueue("ticket", {"ticket": "Cortex#97"})
    assert q.counts() == {"pending": 1, "leased": 0, "done": 0, "dead": 0}
    leased = q.lease("writer-1")
    assert leased is not None
    assert leased["id"] == job["id"]
    assert leased["leased_to"] == "writer-1"
    assert q.counts()["leased"] == 1
    assert q.counts()["pending"] == 0
    acked = q.ack(job["id"])
    assert acked is not None and acked["state"] == "done"
    assert q.counts() == {"pending": 0, "leased": 0, "done": 1, "dead": 0}


def test_nack_returns_pending_or_dead() -> None:
    q = JobQueue()
    first = q.enqueue("ticket", {"ticket": "a"})
    q.lease("w")
    q.nack(first["id"])
    assert q.get(first["id"])["state"] == "pending"
    q.lease("w")
    q.nack(first["id"], dead=True)
    assert q.get(first["id"])["state"] == "dead"
    assert q.counts()["dead"] == 1
    assert q.lease("w") is None


def test_queue_is_not_the_a2a_mailbox() -> None:
    q = JobQueue()
    box = Mailbox()
    q.enqueue("ticket", {"ticket": "x"})
    assert box.empty() is True
    assert q.counts()["pending"] == 1
    assert q.lease("") is None
    assert q.ack("missing") is None
    assert q.nack("missing") is None
