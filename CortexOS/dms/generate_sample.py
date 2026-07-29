"""Generate messy DMS sample datasets — 6 linked tables (~25k clean rows)."""

from __future__ import annotations

import csv
import random
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"

CATEGORIES = [
    "CHEMICALS",
    "ELECTRONICS",
    "FOOD_DRY",
    "FOOD_COLD",
    "PACKAGING",
    "MACHINERY_PARTS",
    "PPE",
    "CLEANING",
    "MEDICAL",
    "RAW_MATERIALS",
]

MALAYSIAN_SITES = [
    ("WH-A", "Shah Alam Central", "Shah Alam", "Selangor", 3.0738, 101.5183),
    ("WH-B", "Penang Distribution", "Bayan Lepas", "Penang", 5.3411, 100.2953),
    ("WH-C", "Johor South Hub", "Pasir Gudang", "Johor", 1.4707, 103.8995),
    ("WH-D", "Klang Logistics Park", "Klang", "Selangor", 3.0449, 101.4455),
    ("WH-E", "Ipoh Highlands DC", "Ipoh", "Perak", 4.5975, 101.0901),
    ("WH-F", "Melaka Gateway", "Melaka", "Melaka", 2.1896, 102.2501),
    ("WH-G", "Kuantan East Store", "Kuantan", "Pahang", 3.8077, 103.3260),
    ("WH-H", "Kota Kinabalu Hub", "Kota Kinabalu", "Sabah", 5.9804, 116.0735),
    ("WH-I", "Kuching Sarawak DC", "Kuching", "Sarawak", 1.5535, 110.3593),
    ("WH-J", "Seremban Central", "Seremban", "Negeri Sembilan", 2.7258, 101.9424),
    ("WH-K", "Alor Setar North", "Alor Setar", "Kedah", 6.1248, 100.3678),
    ("WH-L", "Kota Bharu DC", "Kota Bharu", "Kelantan", 6.1254, 102.2381),
    ("WH-M", "Miri East Store", "Miri", "Sarawak", 4.3995, 113.9914),
    ("WH-N", "Sandakan Port WH", "Sandakan", "Sabah", 5.8394, 118.1171),
    ("WH-O", "Taiping Industrial", "Taiping", "Perak", 4.8519, 100.7415),
    ("WH-P", "Kemaman Chemical Park", "Kemaman", "Terengganu", 4.2330, 103.4478),
    ("WH-Q", "Batu Pahat DC", "Batu Pahat", "Johor", 1.8548, 102.9325),
    ("WH-R", "Kangar Border Store", "Kangar", "Perlis", 6.4410, 100.1986),
    ("WH-S", "Sibu River Hub", "Sibu", "Sarawak", 2.2870, 111.8303),
    ("WH-T", "Putrajaya Gov DC", "Putrajaya", "Putrajaya", 2.9264, 101.6964),
]

LOC_CODE_VARIANTS: dict[str, list[str]] = {}
for code, *_ in MALAYSIAN_SITES:
    letter = code.split("-")[1].lower()
    LOC_CODE_VARIANTS[code] = [
        code,
        f"Warehouse {letter.upper()}",
        f"wh_{letter}",
        f"whse {letter}",
        code.lower(),
    ]

DATE_FORMATS: list[Callable[[date], str]] = [
    lambda d: d.isoformat(),
    lambda d: d.strftime("%d/%m/%Y"),
    lambda d: d.strftime("%b %d %Y"),
]

UNIT_FORMATS: list[Callable[[float], str]] = [
    lambda q: f"{q}kg",
    lambda q: f"{q} KG",
    lambda q: f"{q}kilogram",
    lambda q: str(q),
]

CARRIERS = ["Pos Laju", "GDEX", "City-Link", "J&T Express", "DHL MY", "TNT Malaysia"]
PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "COD", "Net 15"]
COUNTRIES = ["Malaysia"] * 8 + ["China", "Singapore", "Thailand"]

# Guaranteed low-stock at WH-A for demo (8 SKUs).
LOW_STOCK_WH_A = [
    ("SKU-90001", "Industrial Solvent 5L", 2.0, 15.0),
    ("SKU-90002", "Shrink Wrap Film Roll", 5.0, 20.0),
    ("SKU-90003", "Nitrile Gloves Box", 1.0, 12.0),
    ("SKU-90004", "Dust Mask N95 Pack", 4.0, 25.0),
    ("SKU-90005", "Spill Kit 20L", 1.0, 8.0),
    ("SKU-90006", "First Aid Refill", 6.0, 18.0),
    ("SKU-90007", "Stretch Hood Film", 7.0, 30.0),
    ("SKU-90008", "Humidity Sensor Probe", 2.0, 10.0),
]

SKU_NAMES = [
    "Steel Bolt M8",
    "Hydraulic Hose 2in",
    "Industrial Solvent 5L",
    "Forklift Battery 24V",
    "Safety Gloves L",
    "Conveyor Belt Patch",
    "Shrink Wrap Film Roll",
    "Label Printer Ribbon",
    "Dock Leveler Seal",
    "LED Warehouse Light",
    "Nitrile Gloves Box",
    "Pallet Jack Wheel",
    "Barcode Scanner",
    "Fire Extinguisher 4kg",
    "PVC Pipe 50mm",
    "Rubber Gasket Set",
    "Cable Tie Assorted",
    "Dust Mask N95 Pack",
    "Wooden Pallet Std",
    "Steel Shelving Unit",
    "Hand Truck",
    "Spill Kit 20L",
    "Epoxy Resin 1L",
    "Chain Block 1T",
    "Temperature Logger",
    "Anti-Slip Tape Roll",
    "Welding Rod 3.2mm",
    "Compressed Air Hose",
    "First Aid Refill",
    "Racking Beam 2.4m",
    "Stretch Hood Film",
    "Load Cell 500kg",
    "Dock Bumpers Pair",
    "Floor Marking Paint",
    "Battery Charger 24V",
    "Mesh Security Cage",
    "Humidity Sensor Probe",
    "Pallet Strap Tool",
    "Coolant Concentrate",
    "Wheel Chock Rubber",
]


def _loc_id(i: int) -> str:
    return f"LOC-{i:03d}"


def _supplier_id(i: int) -> str:
    return f"SUP-{i:03d}"


def _sku_id(i: int) -> str:
    return f"SKU-{i:05d}"


def _messy_date(d: date, rng: random.Random) -> str:
    return rng.choice(DATE_FORMATS)(d)


def _messy_qty(q: float, rng: random.Random) -> str:
    return rng.choice(UNIT_FORMATS)(q)


def _maybe_null(value: str, rng: random.Random, rate: float = 0.15) -> str:
    if rng.random() < rate:
        return ""
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _add_messy_dupes(
    rows: list[dict[str, str]],
    rng: random.Random,
    rate: float = 0.10,
) -> list[dict[str, str]]:
    out = list(rows)
    n_dup = max(1, int(len(rows) * rate))
    picks = rng.sample(rows, min(n_dup, len(rows)))
    for row in picks:
        dup = dict(row)
        for key in list(dup.keys()):
            val = str(dup[key])
            if key in {"shipment_id", "txn_id", "alert_id", "supplier_id", "location_id"}:
                continue
            if key == "sku" and val:
                dup[key] = val.replace("0", "O") if "0" in val else val + "x"
            elif "kg" in key and val:
                dup[key] = val.replace("kg", "KG") if "kg" in val.lower() else val
        out.append(dup)
    return out


def generate_locations(rng: random.Random) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    high_cap_indices = {0, 1, 2, 5}  # 4 locations >90% capacity
    for i, (code, name, city, state, lat, lng) in enumerate(MALAYSIAN_SITES):
        loc_id = _loc_id(i + 1)
        capacity = rng.randint(8000, 25000)
        if i in high_cap_indices:
            load = int(capacity * rng.uniform(0.91, 0.98))
        else:
            load = int(capacity * rng.uniform(0.35, 0.85))
        rows.append(
            {
                "location_id": loc_id,
                "location_code": code,
                "location_name": name,
                "city": city,
                "state": state,
                "latitude": f"{lat + rng.uniform(-0.02, 0.02):.4f}",
                "longitude": f"{lng + rng.uniform(-0.02, 0.02):.4f}",
                "capacity_kg": str(capacity),
                "current_load_kg": str(load),
                "cctv_camera_id": f"CCTV-{code}-01",
                "is_cold_storage": "true" if code in {"WH-B", "WH-C", "WH-H", "WH-T"} else "false",
                "manager_name": rng.choice(
                    ["Ahmad Rahman", "Lim Wei Ming", "Siti Nurhaliza", "Raj Kumar", "Tan Mei Ling"]
                ),
            }
        )
    return rows


def generate_suppliers(rng: random.Random, n: int = 200) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    high_risk_ids = set(rng.sample(range(1, n + 1), 8))
    start = date(2023, 1, 1)
    for i in range(1, n + 1):
        sup_id = _supplier_id(i)
        country = rng.choice(COUNTRIES)
        risk = rng.uniform(0.75, 0.95) if i in high_risk_ids else rng.uniform(0.05, 0.65)
        audit = start + timedelta(days=rng.randint(0, 400))
        rows.append(
            {
                "supplier_id": sup_id,
                "supplier_name": f"{country} Supplier {i:03d} Sdn Bhd",
                "contact_person": rng.choice(["Lee", "Ahmad", "Kumar", "Tan", "Nurul"]) + f" {i}",
                "email": f"procurement{i}@supplier{i:03d}.example.com",
                "phone": f"+60{rng.randint(10, 19)}{rng.randint(1000000, 9999999)}",
                "country": country,
                "lead_time_days": str(rng.randint(3, 45)),
                "payment_terms": rng.choice(PAYMENT_TERMS),
                "last_audit_date": audit.isoformat(),
                "risk_score": f"{risk:.2f}",
            }
        )
    return rows


def generate_inventory(
    rng: random.Random,
    locations: list[dict[str, str]],
    suppliers: list[dict[str, str]],
    n: int = 8000,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    today = date.today()
    loc_by_code = {r["location_code"]: r for r in locations}
    wh_a = loc_by_code["WH-A"]

    # Seed guaranteed low-stock rows at WH-A.
    for j, (sku, name, qty, reorder) in enumerate(LOW_STOCK_WH_A):
        sup = suppliers[j % len(suppliers)]
        rows.append(
            {
                "sku": sku,
                "sku_name": name,
                "category": CATEGORIES[j % len(CATEGORIES)],
                "supplier_id": sup["supplier_id"],
                "location_id": wh_a["location_id"],
                "storage_bin": f"WH-A-SEED-{j + 1:02d}",
                "quantity_kg": str(qty),
                "reorder_level_kg": str(reorder),
                "unit_cost_myr": f"{rng.uniform(5, 250):.2f}",
                "last_restocked": (today - timedelta(days=rng.randint(5, 60))).isoformat(),
                "expiry_date": "",
                "is_hazardous": "false",
            }
        )

    seen: set[str] = {f"{r['sku']}|{r['location_id']}|{r['storage_bin']}" for r in rows}
    wh_a_id = loc_by_code["WH-A"]["location_id"]
    n_low_target = int(n * 0.15)
    n_expired_target = int(n * 0.05)
    low_stock_count = len(LOW_STOCK_WH_A)

    for i in range(len(rows), n):
        sku = _sku_id((i % 500) + 1)
        loc = rng.choice(locations)
        sup = rng.choice(suppliers)
        bin_id = f"{loc['location_code']}-BIN-{(i % 80) + 1:02d}"
        key = f"{sku}|{loc['location_id']}|{bin_id}"
        if key in seen:
            continue
        seen.add(key)

        reorder = rng.choice([10, 15, 20, 25, 30, 40, 0])
        qty = rng.randint(20, 500)
        at_wh_a = loc["location_id"] == wh_a_id
        if low_stock_count < n_low_target and not at_wh_a:
            if rng.random() < 0.2:
                qty = rng.randint(1, max(1, reorder - 1)) if reorder > 0 else rng.randint(1, 8)
                reorder = max(qty + 1, reorder or rng.randint(10, 30))
                low_stock_count += 1
        elif reorder > 0 and rng.random() < 0.12 and not at_wh_a:
            qty = rng.randint(1, max(1, reorder - 1))
            low_stock_count += 1
        elif at_wh_a and reorder > 0:
            qty = max(qty, int(reorder) + rng.randint(5, 50))

        expiry = ""
        expired_count = sum(1 for r in rows if r.get("expiry_date") and r["expiry_date"] < today.isoformat())
        if expired_count < n_expired_target and rng.random() < 0.08:
            expiry = (today - timedelta(days=rng.randint(1, 120))).isoformat()
        elif rng.random() < 0.25:
            expiry = (today + timedelta(days=rng.randint(30, 400))).isoformat()

        cat = CATEGORIES[i % len(CATEGORIES)]
        rows.append(
            {
                "sku": sku,
                "sku_name": SKU_NAMES[i % len(SKU_NAMES)],
                "category": cat,
                "supplier_id": sup["supplier_id"],
                "location_id": loc["location_id"],
                "storage_bin": bin_id,
                "quantity_kg": str(qty),
                "reorder_level_kg": str(reorder),
                "unit_cost_myr": f"{rng.uniform(2, 800):.2f}",
                "last_restocked": (today - timedelta(days=rng.randint(1, 180))).isoformat(),
                "expiry_date": expiry,
                "is_hazardous": "true" if cat == "CHEMICALS" and rng.random() < 0.4 else "false",
            }
        )

    return rows[:n]


def generate_shipments(
    rng: random.Random,
    inventory: list[dict[str, str]],
    suppliers: list[dict[str, str]],
    locations: list[dict[str, str]],
    n: int = 12000,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    today = date.today()
    statuses = ["PENDING", "IN_TRANSIT", "DELIVERED", "DELAYED", "CANCELLED"]
    weights = [0.15, 0.25, 0.50, 0.07, 0.03]
    delayed_needed, cancelled_needed = 200, 50
    delayed_count = cancelled_count = 0

    for i in range(n):
        inv = rng.choice(inventory)
        dest = rng.choice(locations)
        sup = rng.choice(suppliers)
        if delayed_count < delayed_needed:
            status = "DELAYED"
            delayed_count += 1
        elif cancelled_count < cancelled_needed:
            status = "CANCELLED"
            cancelled_count += 1
        else:
            status = rng.choices(statuses, weights=weights)[0]

        departed = today - timedelta(days=rng.randint(1, 90))
        expected = departed + timedelta(days=rng.randint(2, 14))
        actual = ""
        if status == "DELIVERED":
            actual = (expected + timedelta(days=rng.randint(-1, 2))).isoformat()
        elif status == "DELAYED":
            expected = today - timedelta(days=rng.randint(1, 10))

        rows.append(
            {
                "shipment_id": f"SHP-{100000 + i}",
                "sku": inv["sku"],
                "supplier_id": sup["supplier_id"],
                "destination_location_id": dest["location_id"],
                "quantity_kg": str(rng.randint(5, 500)),
                "status": status,
                "expected_arrival": expected.isoformat() if hasattr(expected, "isoformat") else str(expected),
                "actual_arrival": actual,
                "carrier": rng.choice(CARRIERS),
                "tracking_number": f"TRK{rng.randint(10**9, 10**10 - 1)}",
                "cost_myr": f"{rng.uniform(50, 5000):.2f}",
            }
        )
    return rows


def generate_transactions(
    rng: random.Random,
    inventory: list[dict[str, str]],
    n: int = 5000,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    types = ["IN", "OUT", "ADJUST", "WRITE_OFF"]
    now = datetime.now()
    for i in range(n):
        inv = rng.choice(inventory)
        ts = now - timedelta(hours=rng.randint(1, 720))
        rows.append(
            {
                "txn_id": f"TXN-{200000 + i}",
                "sku": inv["sku"],
                "location_id": inv["location_id"],
                "txn_type": rng.choices(types, weights=[0.4, 0.4, 0.15, 0.05])[0],
                "quantity_kg": str(rng.randint(1, 200)),
                "unit_cost_myr": inv["unit_cost_myr"],
                "operator_id": f"OP-{rng.randint(1, 50):03d}",
                "timestamp": ts.isoformat(timespec="seconds"),
                "reference_doc": f"DOC-{rng.randint(1000, 9999)}" if rng.random() > 0.2 else "",
            }
        )
    return rows


def generate_alerts(
    rng: random.Random,
    inventory: list[dict[str, str]],
    locations: list[dict[str, str]],
    suppliers: list[dict[str, str]],
    n: int = 800,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    types = [
        "LOW_STOCK",
        "EXPIRY_WARNING",
        "CAPACITY_WARNING",
        "SUPPLIER_RISK",
        "SHIPMENT_DELAY",
        "AUDIT_DUE",
    ]
    severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    now = datetime.now()

    # Seed critical/high counts for UI card.
    critical_needed, high_needed = 3, 12
    crit_count = high_count = 0

    for i in range(n):
        alert_type = rng.choice(types)
        inv = rng.choice(inventory)
        loc = rng.choice(locations)
        sup = rng.choice(suppliers)
        if crit_count < critical_needed:
            severity = "CRITICAL"
            crit_count += 1
        elif high_count < high_needed:
            severity = "HIGH"
            high_count += 1
        else:
            severity = rng.choice(severities)

        created = now - timedelta(hours=rng.randint(1, 720))
        resolved = rng.random() < 0.35
        rows.append(
            {
                "alert_id": f"ALT-{300000 + i}",
                "alert_type": alert_type,
                "severity": severity,
                "related_sku": inv["sku"] if alert_type in {"LOW_STOCK", "EXPIRY_WARNING"} else "",
                "related_location": loc["location_id"] if alert_type in {"CAPACITY_WARNING", "LOW_STOCK"} else "",
                "related_supplier": sup["supplier_id"] if alert_type in {"SUPPLIER_RISK", "AUDIT_DUE"} else "",
                "message": f"{alert_type.replace('_', ' ').title()} — review required ({i})",
                "created_at": created.isoformat(timespec="seconds"),
                "resolved": "true" if resolved else "false",
                "resolved_at": (created + timedelta(hours=rng.randint(1, 48))).isoformat() if resolved else "",
            }
        )
    return rows


def _to_messy_locations(rows: list[dict[str, str]], rng: random.Random) -> list[dict[str, str]]:
    messy: list[dict[str, str]] = []
    for row in rows:
        m = dict(row)
        code = row["location_code"]
        m["location_code"] = rng.choice(LOC_CODE_VARIANTS.get(code, [code]))
        if rng.random() < 0.15:
            m["manager_name"] = ""
        messy.append(m)
    return _add_messy_dupes(messy, rng)


def _to_messy_inventory(
    rows: list[dict[str, str]],
    locations: list[dict[str, str]],
    rng: random.Random,
) -> list[dict[str, str]]:
    loc_code_by_id = {r["location_id"]: r["location_code"] for r in locations}
    messy: list[dict[str, str]] = []
    for row in rows:
        m = dict(row)
        loc_id = row["location_id"]
        code = loc_code_by_id.get(loc_id, "WH-A")
        if rng.random() < 0.35:
            m["location_id"] = rng.choice(LOC_CODE_VARIANTS.get(code, [loc_id]))
        m["quantity_kg"] = _messy_qty(float(row["quantity_kg"]), rng)
        if not str(row.get("sku", "")).startswith("SKU-900"):
            m["reorder_level_kg"] = _maybe_null(row["reorder_level_kg"], rng, 0.12)
        if row.get("last_restocked"):
            d = date.fromisoformat(row["last_restocked"])
            m["last_restocked"] = _messy_date(d, rng)
        if row.get("expiry_date"):
            d = date.fromisoformat(row["expiry_date"])
            m["expiry_date"] = _messy_date(d, rng)
        if rng.random() < 0.15:
            m["sku_name"] = ""
        messy.append(m)
    return _add_messy_dupes(messy, rng)


def _to_messy_suppliers(rows: list[dict[str, str]], rng: random.Random) -> list[dict[str, str]]:
    messy: list[dict[str, str]] = []
    for row in rows:
        m = dict(row)
        m["supplier_name"] = row["supplier_name"].upper() if rng.random() < 0.3 else row["supplier_name"]
        m["payment_terms"] = row["payment_terms"].replace("Net", "NET") if rng.random() < 0.2 else row["payment_terms"]
        m["email"] = _maybe_null(row["email"], rng)
        m["phone"] = _maybe_null(row["phone"], rng)
        if row.get("last_audit_date"):
            d = date.fromisoformat(row["last_audit_date"])
            m["last_audit_date"] = _messy_date(d, rng)
        messy.append(m)
    return _add_messy_dupes(messy, rng)


def _to_messy_shipments(rows: list[dict[str, str]], rng: random.Random) -> list[dict[str, str]]:
    messy: list[dict[str, str]] = []
    for row in rows:
        m = dict(row)
        st = row["status"]
        m["status"] = rng.choice([st, st.lower(), st.replace("_", " ")])
        if row.get("expected_arrival"):
            d = date.fromisoformat(row["expected_arrival"])
            m["expected_arrival"] = _messy_date(d, rng)
        m["quantity_kg"] = _messy_qty(float(row["quantity_kg"]), rng)
        messy.append(m)
    return _add_messy_dupes(messy, rng)


def _to_messy_transactions(rows: list[dict[str, str]], rng: random.Random) -> list[dict[str, str]]:
    messy: list[dict[str, str]] = []
    for row in rows:
        m = dict(row)
        m["quantity_kg"] = _messy_qty(float(row["quantity_kg"]), rng)
        m["reference_doc"] = _maybe_null(row.get("reference_doc", ""), rng)
        messy.append(m)
    return _add_messy_dupes(messy, rng)


def _to_messy_alerts(rows: list[dict[str, str]], rng: random.Random) -> list[dict[str, str]]:
    messy: list[dict[str, str]] = []
    for row in rows:
        m = dict(row)
        m["severity"] = row["severity"].lower() if rng.random() < 0.2 else row["severity"]
        messy.append(m)
    return _add_messy_dupes(messy, rng)


def generate_rows(n: int = 200) -> list[dict[str, str]]:
    """Small inventory subset for unit tests (messy-style columns)."""
    rng = random.Random(42)
    locations = generate_locations(rng)
    suppliers = generate_suppliers(rng, n=20)
    inv = generate_inventory(rng, locations, suppliers, n=n)
    loc_code_by_id = {r["location_id"]: r["location_code"] for r in locations}
    messy: list[dict[str, str]] = []
    for idx, row in enumerate(inv):
        m = dict(row)
        code = loc_code_by_id.get(row["location_id"], "WH-A")
        if idx % 3 == 0:
            m["location_id"] = rng.choice(LOC_CODE_VARIANTS.get(code, [code]))
        m["quantity_kg"] = _messy_qty(float(row["quantity_kg"]), rng)
        if rng.random() < 0.12:
            m["reorder_level_kg"] = ""
        if row.get("last_restocked"):
            d = date.fromisoformat(row["last_restocked"])
            m["last_restocked"] = random.choice(DATE_FORMATS)(d)
        messy.append(m)
    if len(messy) >= 3:
        dup = dict(messy[1])
        messy.append(dup)
    return messy


def main() -> None:
    rng = random.Random(42)
    SAMPLES.mkdir(parents=True, exist_ok=True)

    locations = generate_locations(rng)
    suppliers = generate_suppliers(rng, 200)
    inventory = generate_inventory(rng, locations, suppliers, 8000)
    shipments = generate_shipments(rng, inventory, suppliers, locations, 12000)
    transactions = generate_transactions(rng, inventory, 5000)
    alerts = generate_alerts(rng, inventory, locations, suppliers, 800)

    tables: list[tuple[str, list[dict[str, str]], list[str], Callable]] = [
        (
            "locations",
            locations,
            list(locations[0].keys()),
            lambda r: _to_messy_locations(r, random.Random(99)),
        ),
        (
            "suppliers",
            suppliers,
            list(suppliers[0].keys()),
            lambda r: _to_messy_suppliers(r, random.Random(101)),
        ),
        (
            "inventory",
            inventory,
            list(inventory[0].keys()),
            lambda r: _to_messy_inventory(r, locations, random.Random(103)),
        ),
        (
            "shipments",
            shipments,
            list(shipments[0].keys()),
            lambda r: _to_messy_shipments(r, random.Random(107)),
        ),
        (
            "transactions",
            transactions,
            list(transactions[0].keys()),
            lambda r: _to_messy_transactions(r, random.Random(109)),
        ),
        (
            "alerts",
            alerts,
            list(alerts[0].keys()),
            lambda r: _to_messy_alerts(r, random.Random(111)),
        ),
    ]

    for name, clean_rows, fields, messy_fn in tables:
        messy_rows = messy_fn(clean_rows)
        _write_csv(SAMPLES / f"{name}_messy.csv", messy_rows, fields)
        print(f"  {name}_messy.csv: {len(messy_rows)} rows")

    # Legacy alias for data page backward compat during transition.
    _write_csv(SAMPLES / "warehouse_messy.csv", _to_messy_inventory(inventory, locations, random.Random(115)), list(inventory[0].keys()))


if __name__ == "__main__":
    print("Generating DMS v2 sample datasets...")
    main()
    print("Done.")
