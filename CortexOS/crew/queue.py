"""Crew-owned work leases. Control displays counts; Control never POSTs.

Not a second A2A mailbox and not LangGraph. Pending work is leased by Crew,
acked to done, or nacked to dead. Counts (pending / leased / done / dead)
are what Control renders on the conveyor.

The A2A mailbox remains the delivery channel; this queue is the belt's
lease ledger so an operator can see work that is claimed without Control
ever POSTing a wake or a lease.
"""

from __future__ import annotations

import uuid
from typing import Any

STATES = ("pending", "leased", "done", "dead")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class JobQueue:
    """In-process lease queue for one CrewApp."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}

    def enqueue(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = _new_id()
        row = {
            "id": job_id,
            "kind": (kind or "work").strip() or "work",
            "payload": dict(payload or {}),
            "state": "pending",
            "leased_to": None,
        }
        self._jobs[job_id] = row
        return dict(row)

    def lease(self, worker: str) -> dict[str, Any] | None:
        who = (worker or "").strip()
        if not who:
            return None
        for row in self._jobs.values():
            if row["state"] == "pending":
                row["state"] = "leased"
                row["leased_to"] = who
                return dict(row)
        return None

    def ack(self, job_id: str) -> dict[str, Any] | None:
        row = self._jobs.get(job_id)
        if row is None or row["state"] != "leased":
            return None
        row["state"] = "done"
        return dict(row)

    def nack(self, job_id: str, *, dead: bool = False) -> dict[str, Any] | None:
        row = self._jobs.get(job_id)
        if row is None or row["state"] != "leased":
            return None
        if dead:
            row["state"] = "dead"
        else:
            row["state"] = "pending"
            row["leased_to"] = None
        return dict(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._jobs.get(job_id)
        return dict(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        tallies = {name: 0 for name in STATES}
        for row in self._jobs.values():
            state = row["state"]
            if state in tallies:
                tallies[state] += 1
        return tallies
