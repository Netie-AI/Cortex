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
    "sku": "transactions",
    "status": "shipments",
    "carrier": "shipments",
    "country": "suppliers",
    "location_code": "locations",
    "severity": "alerts",
    "txn_type": "transactions",
}
MAX_VALUES = 1000


@dataclass(slots=True)
class Resolution:
    value: str | None
    confidence: float
    column: str
    candidates: list[str]

    @property
    def ok(self) -> bool:
        return self.value is not None and self.confidence >= 0.6

    @property
    def exact(self) -> bool:
        """True when the caller may apply without a confirm chip."""
        return self.value is not None and self.confidence >= 0.95


@lru_cache(maxsize=1)
def _sku_name_index() -> dict[str, str]:
    """sku_name (lower) → sku. Used for \"wolf\" / \"beta trial\" style exclusion phrases."""
    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
        read_only_queries_enabled,
    )

    out: dict[str, str] = {}
    con = get_connection(DEFAULT_DB, read_only=read_only_queries_enabled())
    try:
        rows = con.execute(
            "SELECT sku, sku_name FROM inventory "
            "WHERE sku IS NOT NULL AND sku_name IS NOT NULL "
            f"LIMIT {MAX_VALUES}"
        ).fetchall()
        for sku, name in rows:
            out[str(name).strip().lower()] = str(sku)
    except Exception:  # noqa: BLE001
        return {}
    finally:
        con.close()
    return out


def _sensitive() -> set[str]:
    try:
        from CortexOS.dms.warehouse_db import load_semantic_layer

        return set(load_semantic_layer().get("sensitive_columns") or [])
    except Exception:  # noqa: BLE001
        return set()


@lru_cache(maxsize=1)
def _dictionaries() -> dict[str, list[str]]:
    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
        read_only_queries_enabled,
    )

    sensitive = _sensitive()
    out: dict[str, list[str]] = {}
    con = get_connection(DEFAULT_DB, read_only=read_only_queries_enabled())
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
    _sku_name_index.cache_clear()


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

    # 2b. SKU encoding — bare `BETA` / `00173` → `SKU-BETA` / `SKU-00173`
    if column == "sku":
        candidates = []
        if not lower.startswith("sku"):
            candidates.append(f"SKU-{text.upper()}")
            if text.isdigit():
                candidates.append(f"SKU-{int(text):05d}")
                candidates.append(f"SKU-{text.zfill(5)}")
        else:
            # SKU-BETA already canonical form — still must exist in the dict
            candidates.append(text.upper())
            m = re.match(r"^sku[-\s]?(.+)$", text, flags=re.I)
            if m:
                rest = m.group(1).strip().upper()
                candidates.append(f"SKU-{rest}")
                if rest.isdigit():
                    candidates.append(f"SKU-{int(rest):05d}")
        for cand in candidates:
            if cand in values:
                return Resolution(cand, 0.95, column, [cand])
            if cand.lower() in by_lower:
                return Resolution(by_lower[cand.lower()], 0.95, column, [by_lower[cand.lower()]])
            if norm(cand) in by_norm:
                return Resolution(by_norm[norm(cand)], 0.95, column, [by_norm[norm(cand)]])

        # 2c. sku_name phrase / substring ("beta trial", "wolf", "nitrile gloves")
        name_idx = _sku_name_index()
        if lower in name_idx:
            return Resolution(name_idx[lower], 0.9, column, [name_idx[lower]])
        name_hits = [
            sku
            for name, sku in name_idx.items()
            if lower in name
            or any(len(tok) >= 4 and tok in name for tok in lower.split())
        ]
        # Prefer unique containment of the full phrase in the name.
        phrase_hits = [sku for name, sku in name_idx.items() if lower in name]
        if len(set(phrase_hits)) == 1:
            return Resolution(phrase_hits[0], 0.88, column, [phrase_hits[0]])
        if len(set(name_hits)) == 1:
            return Resolution(name_hits[0], 0.82, column, [name_hits[0]])
        if len(set(phrase_hits)) > 1:
            uniq = list(dict.fromkeys(phrase_hits))
            return Resolution(None, 0.0, column, uniq[:5])

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
