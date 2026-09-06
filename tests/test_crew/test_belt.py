"""Crew ticket leases. Control displays; Control never POSTs."""

from __future__ import annotations

from CortexOS.crew.belt import DEFAULT_LEASE_TTL_S, TicketLeaseLedger, stamp_items


def test_claim_refresh_conflict_and_ttl() -> None:
    ledger = TicketLeaseLedger()
    first = ledger.claim("Netie-AI/Cortex#199", "Scout", ttl_s=60, now=1_000)
    assert first["ok"] is True
    assert first["lease"]["worker"] == "Scout"
    assert first["lease"]["until"] == 1_060
    assert first["lease"]["ttl_s"] == 60
    again = ledger.claim("Netie-AI/Cortex#199", "Scout", ttl_s=90, now=1_010)
    assert again["ok"] is True
    assert again["lease"]["until"] == 1_100
    other = ledger.claim("Netie-AI/Cortex#199", "Gate", now=1_011)
    assert other["ok"] is False
    assert other["reason"] == "conflict"
    assert "held by Scout" in other["detail"]
    assert ledger.live_count(now=1_011) == 1


def test_expired_lease_is_reclaimable() -> None:
    ledger = TicketLeaseLedger()
    ledger.claim("FF-03", "Scout", ttl_s=10, now=50)
    assert ledger.get("FF-03", now=61) is None
    taken = ledger.claim("FF-03", "Gate", ttl_s=10, now=61)
    assert taken["ok"] is True
    assert taken["lease"]["worker"] == "Gate"


def test_release_only_holder() -> None:
    ledger = TicketLeaseLedger()
    ledger.claim("FF-03", "Scout", now=1)
    denied = ledger.release("FF-03", "Gate", now=2)
    assert denied["ok"] is False
    assert denied["reason"] == "forbidden"
    missing = ledger.release("nope", "Scout", now=2)
    assert missing["reason"] == "missing"
    ok = ledger.release("FF-03", "Scout", now=2)
    assert ok["ok"] is True
    assert ledger.public(now=3) == []
    empty = ledger.claim("", "Scout")
    assert empty["reason"] == "bad_id"
    nowho = ledger.claim("FF-03", "  ")
    assert nowho["reason"] == "bad_worker"


def test_stamp_items_is_additive() -> None:
    items = [
        {"spec": "Netie-AI/Cortex#173", "title": "request_close", "ready": True},
        {"title": "FF-03", "ready": True},
    ]
    leases = [{"id": "Netie-AI/Cortex#173", "worker": "Scout", "until": 99}]
    stamped = stamp_items(items, leases)
    assert stamped[0]["lease"] == {"worker": "Scout", "until": 99}
    assert "lease" not in stamped[1]
    assert DEFAULT_LEASE_TTL_S == 900
