"""Generate ``data/samples/warehouse_messy.csv`` (~200 messy inventory rows)."""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "samples" / "warehouse_messy.csv"

SKUS = [
    ("SKU-001", "Steel Bolt M8"),
    ("SKU-002", "Hydraulic Hose 2in"),
    ("SKU-003", "Pallet Wrap Roll"),
    ("SKU-004", "Forklift Battery"),
    ("SKU-005", "Safety Gloves L"),
    ("SKU-006", "Conveyor Belt Patch"),
    ("SKU-007", "Shrink Wrap Film"),
    ("SKU-008", "Label Printer Ribbon"),
    ("SKU-009", "Dock Leveler Seal"),
    ("SKU-010", "LED Warehouse Light"),
]

LOC_VARIANTS = {
    "WH-A": ["WH-A", "Warehouse A", "whse a", "wh_a"],
    "WH-B": ["WH-B", "Warehouse B", "whse b", "wh_b"],
}

SUPPLIERS = [
    ("Acme Corp", ["Acme Corp", "ACME CORP", "Acme corp."]),
    ("Global Parts Ltd", ["Global Parts Ltd", "GLOBAL PARTS LTD", "Global parts ltd"]),
    ("Metro Supply", ["Metro Supply", "METRO SUPPLY", "metro supply co"]),
]

DATE_FORMATS = [
    lambda d: d.isoformat(),
    lambda d: d.strftime("%d/%m/%Y"),
    lambda d: d.strftime("%b %d %Y"),
]

UNIT_FORMATS = [
    lambda q: f"{q}kg",
    lambda q: f"{q} KG",
    lambda q: f"{q}kilogram",
    lambda q: str(q),
]


def _sku_variant(base: str, idx: int) -> str:
    variants = [base, base.replace("-", ""), base.replace("-", " ").lower(), base.lower()]
    return variants[idx % len(variants)]


def generate_rows(n: int = 200) -> list[dict[str, str]]:
    random.seed(42)
    rows: list[dict[str, str]] = []
    start = date(2024, 1, 1)
    for i in range(n):
        sku_base, name = SKUS[i % len(SKUS)]
        loc_key = "WH-A" if i % 2 == 0 else "WH-B"
        supplier_base, supplier_vars = SUPPLIERS[i % len(SUPPLIERS)]
        qty = random.randint(2, 120)
        reorder = random.choice([10, 15, 20, 25, 30, "N/A", ""])
        if random.random() < 0.15:
            reorder = ""
        d = start + timedelta(days=i % 90)
        row = {
            "sku": _sku_variant(sku_base, i // len(SKUS)),
            "product_name": name if random.random() > 0.05 else "",
            "quantity_kg": random.choice(UNIT_FORMATS)(qty),
            "last_updated": random.choice(DATE_FORMATS)(d),
            "location": random.choice(LOC_VARIANTS[loc_key]),
            "reorder_level": str(reorder) if reorder != "" else "",
            "supplier_name": random.choice(supplier_vars),
            "supplier_contact_email": f"contact{i % 12}@supplier.example.com",
        }
        rows.append(row)
        if random.random() < 0.10:
            dup = dict(row)
            dup["sku"] = dup["sku"].replace("0", "O") if "0" in dup["sku"] else dup["sku"] + " "
            dup["quantity_kg"] = random.choice(UNIT_FORMATS)(max(1, qty - 1))
            rows.append(dup)
    return rows


def main() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_rows(200)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return OUT


if __name__ == "__main__":
    print(main())
