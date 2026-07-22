"""Analyse and ingest free-form inventory entries for the DMS demo."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

ROOT = Path(__file__).resolve().parents[2]
CLEAN_CSV = ROOT / "data" / "samples" / "inventory_clean.csv"
CHANGELOG_PATH = ROOT / "data" / "samples" / "warehouse_changelog.jsonl"

_KG = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|kilogram|kilograms)?", re.I)
_LOC_MAP = {
    "warehouse a": "WH-A",
    "wh-a": "WH-A",
    "wh a": "WH-A",
    "wha": "WH-A",
    "warehouse b": "WH-B",
    "wh-b": "WH-B",
    "wh b": "WH-B",
    "whb": "WH-B",
    "warehouse c": "WH-C",
    "wh-c": "WH-C",
    "wh c": "WH-C",
    "whc": "WH-C",
}
_CAPACITY_LIMITS = {"WH-A": 5000.0, "WH-B": 4000.0, "WH-C": 3000.0}


def _normalize_sku(raw: str) -> tuple[str, str | None]:
    original = raw.strip()
    compact = re.sub(r"[\s_]+", "", original.upper())
    if re.match(r"^SKU\d+$", compact):
        num = compact[3:]
        return f"SKU-{num.zfill(3)}", original
    if re.match(r"^SKU-\d+$", compact):
        parts = compact.split("-", 1)
        return f"SKU-{parts[1].zfill(3)}", original if original.upper() != compact else None
    return compact, original if original.upper() != compact else None


def _extract_fields(raw_text: str) -> tuple[dict[str, Any], dict[str, str | None]]:
    text = raw_text.strip()
    originals: dict[str, str | None] = {
        "sku": None,
        "quantity_kg": None,
        "location": None,
        "reorder_level": None,
        "supplier_name": None,
    }

    sku_match = re.search(r"\b(SKU[\s_-]?\d+)\b", text, re.I)
    sku_raw = sku_match.group(1) if sku_match else "SKU-000"
    sku, sku_orig = _normalize_sku(sku_raw)
    originals["sku"] = sku_orig

    qty_match = _KG.search(text)
    quantity = float(qty_match.group(1)) if qty_match else 0.0
    if qty_match:
        originals["quantity_kg"] = qty_match.group(0)

    loc = "WH-A"
    lower = text.lower()
    for key, canon in _LOC_MAP.items():
        if key in lower:
            loc = canon
            originals["location"] = key
            break

    reorder_match = re.search(r"reorder(?:\s+at|\s+level)?\s*(\d+)", text, re.I)
    reorder = int(reorder_match.group(1)) if reorder_match else 0

    supplier = "Unknown Supplier"
    supplier_match = re.search(r"supplier\s+([A-Za-z][A-Za-z0-9 .&-]*)", text, re.I)
    if supplier_match:
        supplier = supplier_match.group(1).strip().rstrip(",")
        originals["supplier_name"] = supplier_match.group(1).strip()

    proposed = {
        "sku": sku,
        "quantity_kg": quantity,
        "location": loc,
        "reorder_level": reorder,
        "supplier_name": supplier.title(),
        "product_name": "",
        "last_updated": datetime.now(timezone.utc).date().isoformat(),
        "supplier_contact_email": "",
    }
    return proposed, originals


def _build_issues(proposed: dict[str, Any], originals: dict[str, str | None]) -> list[str]:
    issues: list[str] = []
    if originals.get("sku"):
        issues.append(f"SKU normalized from {originals['sku']}")
    if originals.get("quantity_kg"):
        issues.append(f"Unit extracted from '{originals['quantity_kg']}'")
    if originals.get("location"):
        issues.append(f"Location canonicalized from '{originals['location']}'")
    if proposed["supplier_name"] == "Unknown Supplier":
        issues.append("Supplier name not detected — default applied")
    return issues


def _hidden_issues(proposed: dict[str, Any]) -> list[str]:
    hidden: list[str] = []
    con = get_connection(DEFAULT_DB)
    try:
        sku = proposed["sku"]
        loc = proposed["location"]
        supplier = proposed["supplier_name"]

        dup = con.execute(
            "SELECT COUNT(*) FROM inventory WHERE sku = ?",
            [sku],
        ).fetchone()[0]
        if dup:
            hidden.append(f"{sku} already exists in inventory (duplicate check)")

        supplier_rows = con.execute(
            "SELECT supplier_name, supplier_contact_email FROM inventory "
            "WHERE lower(supplier_name) LIKE ? LIMIT 3",
            [f"%{supplier.lower()}%"],
        ).fetchall()
        for name, email in supplier_rows:
            if name and name.lower() != supplier.lower():
                hidden.append(
                    f"{supplier} already exists with different contact email on file ({email or 'no email'})"
                )
                break

        cap = _CAPACITY_LIMITS.get(loc, 5000.0)
        used = con.execute(
            "SELECT COALESCE(SUM(quantity_kg), 0) FROM inventory WHERE location = ?",
            [loc],
        ).fetchone()[0]
        pct = int(100 * float(used) / cap) if cap else 0
        if pct >= 90:
            hidden.append(f"{loc} is at {pct}% capacity based on current inventory")
    except Exception:
        pass
    finally:
        con.close()
    return hidden


def analyse_raw_entry(raw_text: str) -> dict[str, Any]:
    proposed, originals = _extract_fields(raw_text)
    return {
        "proposed": proposed,
        "originals": originals,
        "issues": _build_issues(proposed, originals),
        "hidden_issues": _hidden_issues(proposed),
    }


def _append_changelog(row_id: int, col: str, old: Any, new: Any, rule_id: str, approved_by: str) -> None:
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "row_id": row_id,
        "col": col,
        "old_value": old,
        "new_value": new,
        "rule_id": rule_id,
        "approved_by": approved_by,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with CHANGELOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def add_inventory_entry(proposed: dict[str, Any], *, approved_by: str = "demo_steward") -> dict[str, Any]:
    row = {
        "sku": proposed["sku"],
        "product_name": proposed.get("product_name", ""),
        "quantity_kg": proposed["quantity_kg"],
        "last_updated": proposed.get("last_updated", datetime.now(timezone.utc).date().isoformat()),
        "location": proposed["location"],
        "reorder_level": proposed.get("reorder_level", 0),
        "supplier_name": proposed["supplier_name"],
        "supplier_contact_email": proposed.get("supplier_contact_email", ""),
    }

    CLEAN_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not CLEAN_CSV.is_file() or CLEAN_CSV.stat().st_size == 0
    fieldnames = list(row.keys())

    existing_rows: list[dict[str, str]] = []
    if CLEAN_CSV.is_file():
        with CLEAN_CSV.open(newline="", encoding="utf-8-sig") as f:
            existing_rows = list(csv.DictReader(f))

    row_id = len(existing_rows)
    with CLEAN_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow({k: str(v) for k, v in row.items()})

    _append_changelog(row_id, "sku", "", row["sku"], "manual_entry", approved_by)

    con = get_connection(DEFAULT_DB)
    try:
        con.execute(
            "INSERT INTO inventory VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row["sku"],
                row["product_name"],
                float(row["quantity_kg"]),
                row["last_updated"],
                row["location"],
                int(row["reorder_level"]),
                row["supplier_name"],
                row["supplier_contact_email"],
            ],
        )
    finally:
        con.close()

    return {"ok": True, "row": row, "row_id": row_id}
