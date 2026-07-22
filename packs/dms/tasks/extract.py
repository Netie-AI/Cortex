"""T2 field extraction for task templates — LLM may extract; rules decide verdict."""

from __future__ import annotations

import re
from typing import Any


def extract_fields(raw_text: str, task_id: str) -> dict[str, Any]:
    """
    Heuristic extraction from unstructured text. Optional T2 path may replace this.
    Never returns verdict/pass flags that gate.py would trust.
    """
    out: dict[str, Any] = {}
    lower = raw_text.lower()

    money = re.search(r"(?:rm|myr)\s*([\d,]+(?:\.\d+)?)", lower)
    if money:
        out["value_myr"] = float(money.group(1).replace(",", ""))
        out["quote_total_myr"] = out["value_myr"]

    if "pickup" in lower or "collect" in lower:
        out["task_action"] = "schedule_pickup"
        addr = re.search(r"(?:at|to)\s+(\d+[^.]+)", raw_text, re.I)
        if addr:
            out["pickup_address"] = addr.group(1).strip()

    if "quote" in lower or "pricing" in lower:
        out["task_action"] = "send_quote"

    if "send" in lower and "customer" in lower:
        out["task_action"] = "send_outbound"
        out["customer_verified"] = "verified" in lower

    return out
