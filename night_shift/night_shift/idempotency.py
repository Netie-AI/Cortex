"""Idempotency for place-order.

ADK Resume may re-run a tool after a crash (google.github.io/adk-docs/runtime/resume).
That is the webinar trap: a resumable agent can order two laptops. The key is the
order identity, not the invocation id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def po_key(*, vendor: str, sku: str, qty: int, week: str) -> str:
    raw = "|".join(
        [
            vendor.strip().lower(),
            sku.strip().upper(),
            str(int(qty)),
            week.strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class PurchaseOrder:
    key: str
    vendor: str
    sku: str
    qty: int
    week: str
    po_id: str
    status: str
    created_at: str


class OrderLedger:
    """Committed POs. A crash after intent but before commit must not mint a second PO."""

    def __init__(self) -> None:
        self._committed: dict[str, PurchaseOrder] = {}
        self._intents: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def begin_intent(self, key: str, payload: dict[str, Any]) -> None:
        self._intents[key] = {"payload": payload, "at": _now()}

    def commit(self, key: str, vendor: str, sku: str, qty: int, week: str) -> dict[str, Any]:
        existing = self._committed.get(key)
        if existing is not None:
            return {
                "status": "idempotent_replay",
                "did_not_reorder": True,
                "po": asdict(existing),
            }
        self._seq += 1
        po = PurchaseOrder(
            key=key,
            vendor=vendor,
            sku=sku,
            qty=qty,
            week=week,
            po_id=f"PO-{self._seq:04d}",
            status="placed",
            created_at=_now(),
        )
        self._committed[key] = po
        self._intents.pop(key, None)
        return {
            "status": "placed",
            "did_not_reorder": False,
            "po": asdict(po),
        }

    def place(self, *, vendor: str, sku: str, qty: int, week: str) -> dict[str, Any]:
        key = po_key(vendor=vendor, sku=sku, qty=qty, week=week)
        self.begin_intent(key, {"vendor": vendor, "sku": sku, "qty": qty, "week": week})
        return self.commit(key, vendor, sku, qty, week)

    def crash_after_intent(self, *, vendor: str, sku: str, qty: int, week: str) -> str:
        """Leave a dangling intent. Caller must resume via place()."""
        key = po_key(vendor=vendor, sku=sku, qty=qty, week=week)
        self.begin_intent(key, {"vendor": vendor, "sku": sku, "qty": qty, "week": week})
        return key

    def placed_count(self) -> int:
        return len(self._committed)

    def dump(self) -> dict[str, Any]:
        return {
            "committed": [asdict(p) for p in self._committed.values()],
            "open_intents": list(self._intents),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_from_json(blob: str) -> OrderLedger:
    data = json.loads(blob)
    led = OrderLedger()
    for row in data.get("committed", []):
        po = PurchaseOrder(**row)
        led._committed[po.key] = po
        led._seq = max(led._seq, int(po.po_id.split("-")[-1]))
    return led
