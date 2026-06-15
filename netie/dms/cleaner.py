"""Deterministic cleaning pipeline with audited rule application."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from netie.dms.profiler import profile_dataset

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHANGELOG = ROOT / "data" / "samples" / "warehouse_changelog.jsonl"
DEFAULT_CLEAN = ROOT / "data" / "samples" / "warehouse_clean.csv"

_KG = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:kg|kilogram|kilograms)?\s*$", re.I)
_LOC_MAP = {
    "wha": "WH-A",
    "warehousea": "WH-A",
    "whsea": "WH-A",
    "whb": "WH-B",
    "warehouseb": "WH-B",
    "whseb": "WH-B",
}


def _normalize_sku(raw: str) -> str:
    s = re.sub(r"\s+", "", raw.upper())
    m = re.match(r"SKU(\d{3})", s.replace("-", ""))
    if m:
        return f"SKU-{m.group(1)}"
    return s


def _normalize_unit(raw: str) -> float | None:
    if not raw or not str(raw).strip():
        return None
    m = _KG.match(str(raw).strip())
    if m:
        return float(m.group(1))
    if str(raw).strip().replace(".", "", 1).isdigit():
        return float(str(raw).strip())
    return None


def _normalize_date(raw: str) -> str:
    from datetime import datetime as dt

    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%b %d %Y"):
        try:
            return dt.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _canonical_location(raw: str) -> str:
    key = re.sub(r"[\s_]", "", raw.lower())
    if key in _LOC_MAP:
        return _LOC_MAP[key]
    if key.startswith("wh") and "a" in key:
        return "WH-A"
    if key.startswith("wh") and "b" in key:
        return "WH-B"
    return raw.strip().upper()


def _propose_rules(profile: dict[str, Any]) -> list[dict[str, str]]:
    """Demo propose step — deterministic rules from profile (LLM would propose in prod)."""
    return [
        {"id": "normalize_units", "description": "Normalize quantity_kg to float kg"},
        {"id": "normalize_sku", "description": "Canonical SKU format SKU-NNN"},
        {"id": "normalize_dates", "description": "Coerce last_updated to ISO 8601"},
        {"id": "canonicalize_location", "description": "Map location variants to WH-A/WH-B"},
        {"id": "dedup_fuzzy", "description": "Dedupe by normalized sku + location"},
        {"id": "fill_nulls", "description": "Fill null reorder_level with 0"},
    ]


def _append_changelog(
    path: Path,
    *,
    row_id: int,
    col: str,
    old_value: Any,
    new_value: Any,
    rule_id: str,
    approved_by: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
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


def clean_dataset(
    input_path: Path | str,
    *,
    output_path: Path | str | None = None,
    changelog_path: Path | str | None = None,
    human_approver: str = "demo_auto",
) -> Path:
    input_path = Path(input_path)
    output_path = Path(output_path or DEFAULT_CLEAN)
    changelog_path = Path(changelog_path or DEFAULT_CHANGELOG)
    if changelog_path.exists():
        changelog_path.unlink()

    profile = profile_dataset(input_path)
    proposed = _propose_rules(profile.to_dict())
    approved = {r["id"]: r for r in proposed}

    with input_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    transforms: list[tuple[str, Callable[[dict[str, str], int], None]]] = []

    if "normalize_units" in approved:

        def apply_units(row: dict[str, str], idx: int) -> None:
            old = row.get("quantity_kg", "")
            new_v = _normalize_unit(old)
            if new_v is not None and str(old) != str(new_v):
                row["quantity_kg"] = str(new_v)
                _append_changelog(
                    changelog_path,
                    row_id=idx,
                    col="quantity_kg",
                    old_value=old,
                    new_value=new_v,
                    rule_id="normalize_units",
                    approved_by=human_approver,
                )

        transforms.append(("normalize_units", apply_units))

    if "normalize_sku" in approved:

        def apply_sku(row: dict[str, str], idx: int) -> None:
            old = row.get("sku", "")
            new_v = _normalize_sku(old)
            if new_v != old:
                row["sku"] = new_v
                _append_changelog(
                    changelog_path,
                    row_id=idx,
                    col="sku",
                    old_value=old,
                    new_value=new_v,
                    rule_id="normalize_sku",
                    approved_by=human_approver,
                )

        transforms.append(("normalize_sku", apply_sku))

    if "normalize_dates" in approved:

        def apply_dates(row: dict[str, str], idx: int) -> None:
            old = row.get("last_updated", "")
            if not old.strip():
                return
            new_v = _normalize_date(old)
            if new_v != old:
                row["last_updated"] = new_v
                _append_changelog(
                    changelog_path,
                    row_id=idx,
                    col="last_updated",
                    old_value=old,
                    new_value=new_v,
                    rule_id="normalize_dates",
                    approved_by=human_approver,
                )

        transforms.append(("normalize_dates", apply_dates))

    if "canonicalize_location" in approved:

        def apply_loc(row: dict[str, str], idx: int) -> None:
            old = row.get("location", "")
            new_v = _canonical_location(old)
            if new_v != old:
                row["location"] = new_v
                _append_changelog(
                    changelog_path,
                    row_id=idx,
                    col="location",
                    old_value=old,
                    new_value=new_v,
                    rule_id="canonicalize_location",
                    approved_by=human_approver,
                )

        transforms.append(("canonicalize_location", apply_loc))

    if "fill_nulls" in approved:

        def apply_nulls(row: dict[str, str], idx: int) -> None:
            old = row.get("reorder_level", "")
            if old is None or not str(old).strip() or str(old).upper() == "N/A":
                row["reorder_level"] = "0"
                _append_changelog(
                    changelog_path,
                    row_id=idx,
                    col="reorder_level",
                    old_value=old,
                    new_value="0",
                    rule_id="fill_nulls",
                    approved_by=human_approver,
                )

        transforms.append(("fill_nulls", apply_nulls))

    for idx, row in enumerate(rows):
        for _, fn in transforms:
            fn(row, idx)

    if "dedup_fuzzy" in approved:
        kept: dict[str, dict[str, str]] = {}
        order: list[str] = []
        for idx, row in enumerate(rows):
            key = f"{row.get('sku', '')}|{row.get('location', '')}"
            if key not in kept:
                kept[key] = row
                order.append(key)
            else:
                _append_changelog(
                    changelog_path,
                    row_id=idx,
                    col="*",
                    old_value=key,
                    new_value="DROPPED",
                    rule_id="dedup_fuzzy",
                    approved_by=human_approver,
                )
        rows = [kept[k] for k in order]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def main() -> None:
    import sys

    from netie.dms.generate_sample import main as gen

    messy = ROOT / "data" / "samples" / "warehouse_messy.csv"
    if not messy.exists():
        gen()
    out = clean_dataset(messy)
    print(f"Cleaned -> {out}")


if __name__ == "__main__":
    main()
