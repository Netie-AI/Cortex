"""Q1 — value dictionaries + entity resolution for the governed metric layer.

For each categorical column we cache SELECT DISTINCT (capped) and resolve a bit
of NL text to a real value with a confidence score. PII columns (semantic layer
`sensitive_columns`) are never dictionaried. Location resolution handles the
demo's messy dual coding (`WH-A` alongside `WAREHOUSE C`) deterministically.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from functools import lru_cache

# Column → source table for the DISTINCT scan.
VALUE_COLUMNS: dict[str, str] = {
    "category": "inventory",
    "status": "shipments",
    "carrier": "shipments",
    "country": "suppliers",
    "location_code": "locations",
    "severity": "alerts",
    "txn_type": "transactions",
}
MAX_VALUES = 200


@dataclass(slots=True)
class Resolution:
    value: str | None
    confidence: float
    column: str
    candidates: list[str]

    @property
    def ok(self) -> bool:
        return self.value is not None and self.confidence >= 0.6


def _sensitive() -> set[str]:
    try:
        from CortexOS.dms.warehouse_db import load_semantic_layer

        return set(load_semantic_layer().get("sensitive_columns") or [])
    except Exception:  # noqa: BLE001
        return set()


@lru_cache(maxsize=1)
def _dictionaries() -> dict[str, list[str]]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    sensitive = _sensitive()
    out: dict[str, list[str]] = {}
    con = get_connection(DEFAULT_DB)
    try:
        for col, table in VALUE_COLUMNS.items():
            if col in sensitive:
                continue
            try:
                rows = con.execute(
                    f"SELECT DISTINCT {col} FROM {table} "
                    f"WHERE {col} IS NOT NULL ORDER BY {col} LIMIT {MAX_VALUES}"
                ).fetchall()
                out[col] = [str(r[0]) for r in rows]
            except Exception:  # noqa: BLE001 — a missing column just yields no dict
                continue
    finally:
        con.close()
    return out


def refresh() -> None:
    _dictionaries.cache_clear()


def values_for(column: str) -> list[str]:
    return list(_dictionaries().get(column, []))


def all_dictionaries() -> dict[str, list[str]]:
    return {k: list(v) for k, v in _dictionaries().items()}


_WH_LETTER = re.compile(r"\b(?:wh[-\s]?|warehouse\s+)([a-t])\b", re.I)


def _location_candidates(text: str, values: list[str]) -> str | None:
    m = _WH_LETTER.search(text)
    if not m:
        return None
    letter = m.group(1).upper()
    for form in (f"WH-{letter}", f"WAREHOUSE {letter}"):
        if form in values:
            return form
    return None


def resolve(entity_text: str, column: str) -> Resolution:
    """Resolve free NL text to a real column value. Deterministic ladder:
    exact (ci) → normalized → location letter → substring → difflib fuzzy."""
    values = values_for(column)
    if not values:
        return Resolution(None, 0.0, column, [])
    text = (entity_text or "").strip()
    if not text:
        return Resolution(None, 0.0, column, values[:10])

    lower = text.lower()
    by_lower = {v.lower(): v for v in values}

    # 1. exact case-insensitive
    if lower in by_lower:
        return Resolution(by_lower[lower], 1.0, column, [by_lower[lower]])

    # 2. normalized (spaces/underscores/hyphens folded)
    def norm(s: str) -> str:
        return re.sub(r"[\s_\-]+", "", s.lower())

    by_norm = {norm(v): v for v in values}
    if norm(text) in by_norm:
        return Resolution(by_norm[norm(text)], 0.95, column, [by_norm[norm(text)]])

    # 3. location dual-coding
    if column == "location_code":
        hit = _location_candidates(text, values)
        if hit:
            return Resolution(hit, 0.95, column, [hit])

    # 4. token/substring containment (both directions)
    contained = [v for v in values if lower in v.lower() or v.lower() in lower]
    if len(contained) == 1:
        return Resolution(contained[0], 0.85, column, contained)

    # 5. difflib fuzzy over values and their normalized forms
    close = difflib.get_close_matches(text.upper(), values, n=3, cutoff=0.6)
    if close:
        ratio = difflib.SequenceMatcher(None, text.upper(), close[0]).ratio()
        return Resolution(close[0], round(ratio, 3), column, close)

    # ambiguous / not found
    return Resolution(None, 0.0, column, (contained or values)[:5])
