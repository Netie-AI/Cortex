"""Deterministic cleaning pipeline with audited rule application (6 tables)."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from CortexOS.dms.profiler import profile_dataset

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"
DEFAULT_CHANGELOG = SAMPLES / "inventory_changelog.jsonl"

TABLES = ("inventory", "suppliers", "locations", "shipments", "transactions", "alerts")

_KG = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:kg|kilogram|kilograms)?\s*$", re.I)
_WH_CODE_TO_LOC: dict[str, str] = {f"WH-{chr(ord('A') + i)}": f"LOC-{i + 1:03d}" for i in range(20)}
_LOC_MAP: dict[str, str] = {}
for code, loc_id in _WH_CODE_TO_LOC.items():
    letter = code.split("-")[1].lower()
    _LOC_MAP[code.lower()] = loc_id
    _LOC_MAP[loc_id.lower()] = loc_id
    _LOC_MAP[f"wh{letter}"] = loc_id
    _LOC_MAP[f"wh_{letter}"] = loc_id
    _LOC_MAP[f"warehouse{letter}"] = loc_id
    _LOC_MAP[f"whse{letter}"] = loc_id


def _normalize_sku(raw: str) -> str:
    s = re.sub(r"\s+", "", raw.upper())
    m = re.match(r"SKU-?(\d+)", s.replace("O", "0"))
    if m:
        return f"SKU-{int(m.group(1)):05d}"
    return s


def _normalize_unit(raw: str) -> float | None:
    if not raw or not str(raw).strip():
        return None
    m = _KG.match(str(raw).strip())
    if m:
        return float(m.group(1))
    cleaned = str(raw).strip().replace("O", "0")
    if cleaned.replace(".", "", 1).isdigit():
        return float(cleaned)
    return None


def _normalize_date(raw: str) -> str:
    from datetime import datetime as dt

    raw = raw.strip()
    if not raw:
        return raw
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%b %d %Y"):
        try:
            return dt.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _canonical_location_id(raw: str) -> str:
    key = re.sub(r"[\s_]", "", raw.lower()).replace("o", "0")
    if key in _LOC_MAP:
        return _LOC_MAP[key]
    if re.match(r"^loc\d+$", key):
        num = re.sub(r"\D", "", key)
        return f"LOC-{int(num):03d}"
    upper = raw.strip().upper()
    if upper.startswith("LOC-"):
        return upper
    # WH-A style code passed as location_id
    if upper.startswith("WH-") and upper in _WH_CODE_TO_LOC:
        return _WH_CODE_TO_LOC[upper]
    return raw.strip()


def _canonical_location_code(raw: str) -> str:
    loc_id = _canonical_location_id(raw)
    if loc_id in _WH_CODE_TO_LOC.values():
        for code, lid in _WH_CODE_TO_LOC.items():
            if lid == loc_id:
                return code
    upper = raw.strip().upper()
    if upper.startswith("WH-"):
        return upper
    return raw.strip().upper()


def _normalize_status(raw: str) -> str:
    return re.sub(r"\s+", "_", raw.strip().upper())


def _normalize_bool(raw: str) -> str:
    return "true" if str(raw).strip().lower() in {"true", "1", "yes", "y"} else "false"


def _propose_rules(profile: dict[str, Any]) -> list[dict[str, str]]:
    del profile
    return [
        {"id": "normalize_units", "description": "Normalize kg fields to float"},
        {"id": "normalize_sku", "description": "Canonical SKU format"},
        {"id": "normalize_dates", "description": "Coerce dates to ISO 8601"},
        {"id": "canonicalize_location", "description": "Map location variants to LOC-xxx"},
        {"id": "dedup_fuzzy", "description": "Dedupe by natural key"},
        {"id": "fill_nulls", "description": "Fill null reorder_level_kg with 0"},
    ]


def _append_changelog(
    path: Path,
    *,
    table: str,
    row_id: int,
    col: str,
    old_value: Any,
    new_value: Any,
    rule_id: str,
    approved_by: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "table": table,
        "row_id": row_id,
        "col": col,
        "old_value": old_value,
        "new_value": new_value,
        "rule_id": rule_id,
        "approved_by": approved_by,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _apply_field_transforms(
    rows: list[dict[str, str]],
    *,
    table: str,
    changelog_path: Path,
    human_approver: str,
    kg_cols: tuple[str, ...] = (),
    date_cols: tuple[str, ...] = (),
    loc_cols: tuple[str, ...] = (),
    dedup_key: Callable[[dict[str, str]], str] | None = None,
) -> list[dict[str, str]]:
    approved = {r["id"]: r for r in _propose_rules({})}

    for idx, row in enumerate(rows):
        if "normalize_sku" in approved and "sku" in row:
            old, new = row["sku"], _normalize_sku(row["sku"])
            if new != old:
                row["sku"] = new
                _append_changelog(
                    changelog_path,
                    table=table,
                    row_id=idx,
                    col="sku",
                    old_value=old,
                    new_value=new,
                    rule_id="normalize_sku",
                    approved_by=human_approver,
                )
        for col in kg_cols:
            if col not in row:
                continue
            old = row[col]
            new_v = _normalize_unit(old)
            if new_v is not None and str(old) != str(new_v):
                row[col] = str(new_v)
                _append_changelog(
                    changelog_path,
                    table=table,
                    row_id=idx,
                    col=col,
                    old_value=old,
                    new_value=new_v,
                    rule_id="normalize_units",
                    approved_by=human_approver,
                )
        for col in date_cols:
            if col not in row or not str(row.get(col, "")).strip():
                continue
            old = row[col]
            new_v = _normalize_date(old)
            if new_v != old:
                row[col] = new_v
                _append_changelog(
                    changelog_path,
                    table=table,
                    row_id=idx,
                    col=col,
                    old_value=old,
                    new_value=new_v,
                    rule_id="normalize_dates",
                    approved_by=human_approver,
                )
        for col in loc_cols:
            if col not in row:
                continue
            old = row[col]
            new_v = _canonical_location_id(old)
            if new_v != old:
                row[col] = new_v
                _append_changelog(
                    changelog_path,
                    table=table,
                    row_id=idx,
                    col=col,
                    old_value=old,
                    new_value=new_v,
                    rule_id="canonicalize_location",
                    approved_by=human_approver,
                )
        if table == "inventory" and "fill_nulls" in approved:
            old = row.get("reorder_level_kg", "")
            if old is None or not str(old).strip() or str(old).upper() in {"N/A", "TBC"}:
                row["reorder_level_kg"] = "0"
                _append_changelog(
                    changelog_path,
                    table=table,
                    row_id=idx,
                    col="reorder_level_kg",
                    old_value=old,
                    new_value="0",
                    rule_id="fill_nulls",
                    approved_by=human_approver,
                )

    if dedup_key and "dedup_fuzzy" in approved:
        kept: dict[str, dict[str, str]] = {}
        order: list[str] = []
        for idx, row in enumerate(rows):
            key = dedup_key(row)
            if key not in kept:
                kept[key] = row
                order.append(key)
            else:
                _append_changelog(
                    changelog_path,
                    table=table,
                    row_id=idx,
                    col="*",
                    old_value=key,
                    new_value="DROPPED",
                    rule_id="dedup_fuzzy",
                    approved_by=human_approver,
                )
        rows = [kept[k] for k in order]

    return rows


def clean_table(
    table: str,
    *,
    human_approver: str = "demo_auto",
    changelog_path: Path | None = None,
) -> Path:
    messy = SAMPLES / f"{table}_messy.csv"
    clean = SAMPLES / f"{table}_clean.csv"
    log = changelog_path or DEFAULT_CHANGELOG

    if not messy.is_file():
        raise FileNotFoundError(messy)

    with messy.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    if table == "inventory":
        rows = _apply_field_transforms(
            rows,
            table=table,
            changelog_path=log,
            human_approver=human_approver,
            kg_cols=("quantity_kg", "reorder_level_kg"),
            date_cols=("last_restocked", "expiry_date"),
            loc_cols=("location_id",),
            dedup_key=lambda r: f"{r.get('sku', '')}|{r.get('location_id', '')}|{r.get('storage_bin', '')}",
        )
    elif table == "suppliers":
        for row in rows:
            row["supplier_name"] = row.get("supplier_name", "").strip().title()
            row["payment_terms"] = re.sub(r"^net\s*", "Net ", row.get("payment_terms", "").strip(), flags=re.I)
            if row.get("lead_time_days", "").strip().replace(".", "", 1).isdigit():
                row["lead_time_days"] = str(int(float(row["lead_time_days"])))
            row["risk_score"] = f"{float(row.get('risk_score', 0) or 0):.2f}"
        rows = _apply_field_transforms(
            rows,
            table=table,
            changelog_path=log,
            human_approver=human_approver,
            date_cols=("last_audit_date",),
            dedup_key=lambda r: r.get("supplier_id", ""),
        )
    elif table == "locations":
        for row in rows:
            row["location_id"] = _canonical_location_id(row.get("location_id", ""))
            row["location_code"] = _canonical_location_code(row.get("location_code", row.get("location_id", "")))
            row["is_cold_storage"] = _normalize_bool(row.get("is_cold_storage", "false"))
            for c in ("latitude", "longitude"):
                if row.get(c, "").strip():
                    row[c] = f"{float(row[c]):.4f}"
            for c in ("capacity_kg", "current_load_kg"):
                if row.get(c, "").strip():
                    row[c] = str(int(float(row[c])))
        rows = _apply_field_transforms(
            rows,
            table=table,
            changelog_path=log,
            human_approver=human_approver,
            dedup_key=lambda r: r.get("location_id", ""),
        )
    elif table == "shipments":
        for row in rows:
            row["status"] = _normalize_status(row.get("status", ""))
        rows = _apply_field_transforms(
            rows,
            table=table,
            changelog_path=log,
            human_approver=human_approver,
            kg_cols=("quantity_kg",),
            date_cols=("expected_arrival", "actual_arrival"),
            loc_cols=("destination_location_id",),
            dedup_key=lambda r: r.get("shipment_id", ""),
        )
    elif table == "transactions":
        for row in rows:
            row["txn_type"] = row.get("txn_type", "").strip().upper()
        rows = _apply_field_transforms(
            rows,
            table=table,
            changelog_path=log,
            human_approver=human_approver,
            kg_cols=("quantity_kg",),
            date_cols=("timestamp",),
            loc_cols=("location_id",),
            dedup_key=lambda r: r.get("txn_id", ""),
        )
    elif table == "alerts":
        for row in rows:
            row["severity"] = row.get("severity", "").strip().upper()
            row["alert_type"] = row.get("alert_type", "").strip().upper()
            row["resolved"] = _normalize_bool(row.get("resolved", "false"))
        rows = _apply_field_transforms(
            rows,
            table=table,
            changelog_path=log,
            human_approver=human_approver,
            date_cols=("created_at", "resolved_at"),
            dedup_key=lambda r: r.get("alert_id", ""),
        )

    clean.parent.mkdir(parents=True, exist_ok=True)
    with clean.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return clean


def clean_dataset(
    input_path: Path | str,
    *,
    output_path: Path | str | None = None,
    changelog_path: Path | str | None = None,
    human_approver: str = "demo_auto",
) -> Path:
    """Backward-compatible single-file inventory clean."""
    input_path = Path(input_path)
    output_path = Path(output_path or SAMPLES / "inventory_clean.csv")
    changelog_path = Path(changelog_path or DEFAULT_CHANGELOG)
    if changelog_path.exists():
        changelog_path.unlink()

    profile = profile_dataset(input_path)
    del profile

    with input_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    rows = _apply_field_transforms(
        rows,
        table="inventory",
        changelog_path=changelog_path,
        human_approver=human_approver,
        kg_cols=("quantity_kg", "reorder_level_kg"),
        date_cols=("last_restocked", "expiry_date"),
        loc_cols=("location_id",),
        dedup_key=lambda r: f"{r.get('sku', '')}|{r.get('location_id', '')}|{r.get('storage_bin', '')}",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def clean_all_tables(human_approver: str = "demo_auto") -> list[Path]:
    if DEFAULT_CHANGELOG.exists():
        DEFAULT_CHANGELOG.unlink()
    outputs: list[Path] = []
    for table in TABLES:
        outputs.append(clean_table(table, human_approver=human_approver))
    # Legacy alias
    inv_clean = SAMPLES / "inventory_clean.csv"
    legacy = SAMPLES / "warehouse_clean.csv"
    if inv_clean.is_file():
        legacy.write_text(inv_clean.read_text(encoding="utf-8"), encoding="utf-8")
    legacy_log = SAMPLES / "warehouse_changelog.jsonl"
    if DEFAULT_CHANGELOG.is_file():
        legacy_log.write_text(DEFAULT_CHANGELOG.read_text(encoding="utf-8"), encoding="utf-8")
    return outputs


def main() -> None:
    from CortexOS.dms.generate_sample import main as gen

    if not (SAMPLES / "inventory_messy.csv").is_file():
        gen()
    paths = clean_all_tables()
    for p in paths:
        print(f"Cleaned -> {p}")


if __name__ == "__main__":
    main()
