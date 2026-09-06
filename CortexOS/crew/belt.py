"""Crew ticket leases for the belt. Control GET-displays; Control never POSTs.

FIFO work items stay on :class:`CortexOS.crew.queue.JobQueue`. This ledger is
the ticket-id SoT chrome Claim/Release talk to: worker identity plus TTL.
Expired rows are absent. Ticket Runner SEATED seats are refused at the HTTP
edge, not here. Does not write CLAIMS.json and does not set GitHub assignees.
"""

from __future__ import annotations

import time
from typing import Any

DEFAULT_LEASE_TTL_S = 900
MAX_LEASE_TTL_S = 86400


def clamp_ttl_s(raw: int | None) -> int:
    if raw is None:
        return DEFAULT_LEASE_TTL_S
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LEASE_TTL_S
    return max(1, min(MAX_LEASE_TTL_S, n))


def lease_id(raw: str) -> str:
    from CortexOS.crew import github as github_mod

    return github_mod.canonical_spec(raw)


class TicketLeaseLedger:
    """In-process ticket leases for one CrewApp. Not durable across death."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def _now(self, now: float | None) -> float:
        return time.time() if now is None else now

    def _purge(self, now: float) -> None:
        dead = [key for key, row in self._rows.items() if int(row["until"]) <= int(now)]
        for key in dead:
            del self._rows[key]

    def _public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "worker": row["worker"],
            "ttl_s": row["ttl_s"],
            "until": row["until"],
        }

    def get(self, ticket_id: str, *, now: float | None = None) -> dict[str, Any] | None:
        clock = self._now(now)
        self._purge(clock)
        key = lease_id(ticket_id)
        row = self._rows.get(key)
        return self._public_row(row) if row is not None else None

    def public(self, *, now: float | None = None) -> list[dict[str, Any]]:
        clock = self._now(now)
        self._purge(clock)
        return [self._public_row(row) for row in self._rows.values()]

    def live_count(self, *, now: float | None = None) -> int:
        return len(self.public(now=now))

    def claim(
        self,
        ticket_id: str,
        worker: str,
        ttl_s: int | None = None,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Claim or refresh. Conflict when another worker still holds it."""
        key = lease_id(ticket_id)
        who = (worker or "").strip()
        if not key:
            return {"ok": False, "reason": "bad_id", "detail": "DENIED: empty ticket id"}
        if not who:
            return {
                "ok": False,
                "reason": "bad_worker",
                "detail": "DENIED: worker identity required",
            }
        clock = self._now(now)
        self._purge(clock)
        ttl = clamp_ttl_s(ttl_s)
        held = self._rows.get(key)
        if held is not None and held["worker"] != who:
            return {
                "ok": False,
                "reason": "conflict",
                "detail": f"DENIED: held by {held['worker']} until {held['until']}",
                "lease": self._public_row(held),
            }
        row = {
            "id": key,
            "worker": who,
            "ttl_s": ttl,
            "until": int(clock) + ttl,
        }
        self._rows[key] = row
        return {
            "ok": True,
            "reason": "ok",
            "detail": f"Leased {key} to {who} for {ttl}s",
            "lease": self._public_row(row),
        }

    def release(
        self,
        ticket_id: str,
        worker: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Release only the holder. Missing -> 409. Other worker -> 403."""
        key = lease_id(ticket_id)
        who = (worker or "").strip()
        if not key:
            return {"ok": False, "reason": "bad_id", "detail": "DENIED: empty ticket id"}
        if not who:
            return {
                "ok": False,
                "reason": "bad_worker",
                "detail": "DENIED: worker identity required",
            }
        clock = self._now(now)
        self._purge(clock)
        held = self._rows.get(key)
        if held is None:
            return {
                "ok": False,
                "reason": "missing",
                "detail": f"DENIED: {key} is not leased",
            }
        if held["worker"] != who:
            return {
                "ok": False,
                "reason": "forbidden",
                "detail": f"DENIED: held by {held['worker']}. Only the holder may release.",
                "lease": self._public_row(held),
            }
        del self._rows[key]
        return {
            "ok": True,
            "reason": "ok",
            "detail": f"Released {key}",
            "lease": self._public_row(held),
        }


def stamp_items(
    items: list[dict[str, Any]],
    leases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add lease onto a belt ticket item only when one is live. Additive."""
    held = {
        str(row.get("id") or ""): row
        for row in leases
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("spec") or item.get("title") or "").strip()
        lease = held.get(ident)
        if lease is None:
            out.append(item)
            continue
        stamped = dict(item)
        stamped["lease"] = {"worker": lease.get("worker"), "until": lease.get("until")}
        out.append(stamped)
    return out
