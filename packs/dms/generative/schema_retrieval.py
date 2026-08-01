"""C7-full — embed catalog fragments; retrieve top-k tables for L2 prompts.

Never put the full catalog in a prompt. Cache embeddings; invalidate when
``semantic_layer.yaml`` mtime changes.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from packs.dms.skills.capture import cosine_similarity, text_embedding

ROOT = Path(__file__).resolve().parents[3]
SEMANTIC_PATH = ROOT / "packs" / "dms" / "semantic_layer.yaml"
DEFAULT_TOP_K = 4

#: Tables named after FROM / JOIN in a metric's SQL.
TABLE_HINT_RE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.I)


@dataclass(slots=True)
class CatalogEntry:
    table: str
    text: str
    columns: list[str]
    embedding: list[float]


@dataclass
class _Cache:
    mtime_ns: int = 0
    entries: list[CatalogEntry] = field(default_factory=list)
    fingerprint: str = ""


_CACHE = _Cache()


def _glossary_bits(raw: dict[str, Any], table: str) -> list[str]:
    bits: list[str] = []
    for g in raw.get("glossary") or []:
        if not isinstance(g, dict):
            continue
        if g.get("table") == table or table in str(g.get("definition") or "").lower():
            term = str(g.get("term") or "")
            maps = str(g.get("maps_to") or g.get("definition") or "")
            if term:
                bits.append(f"{term}: {maps}")
    return bits


def _metric_vocabulary() -> dict[str, list[str]]:
    """Business vocabulary per table, taken from the metrics that read it.

    Retrieval text used to be purely structural — ``table transactions columns
    txn_id sku location_id txn_type quantity_kg unit_cost_myr …``. The word
    "revenue" appears nowhere in that, so no embedding could connect a revenue
    question to the table revenue lives in: ``transactions`` was never retrieved
    for *any* sales question, and the model dutifully wrote revenue against
    ``inventory`` instead. Wrong table, valid SQL, passes EXPLAIN.

    The vocabulary already exists. ``metrics.yaml`` says ``sales_by_value``
    reads ``transactions`` and is also called "top selling", "revenue by sku",
    "highest revenue" — the same synonyms the alias-graph invariant keeps unique.
    Deriving the description from the metric definitions means there is no third
    place to keep in sync, and a new metric improves retrieval for free.
    """
    try:
        from packs.dms.semantic.loader import load_all
    except Exception:  # noqa: BLE001 — retrieval must not die on a pack import
        return {}

    vocab: dict[str, list[str]] = {}
    try:
        metrics = load_all()
    except Exception:  # noqa: BLE001
        return {}

    declared = getattr(metrics, "metrics", None) or {}
    # ``SemanticModel.metrics`` is keyed by id — iterate the definitions, not the ids.
    for metric in (declared.values() if isinstance(declared, dict) else declared):
        sql = str(getattr(metric, "sql", "") or "").lower()
        words = [str(getattr(metric, "id", ""))] + [
            str(s) for s in (getattr(metric, "synonyms", None) or [])
        ]
        for table in TABLE_HINT_RE.findall(sql):
            vocab.setdefault(table, []).extend(w for w in words if w)
    # Deduplicate, keep order — repeated phrases skew the embedding.
    return {t: list(dict.fromkeys(ws)) for t, ws in vocab.items()}


def _build_entries(raw: dict[str, Any]) -> list[CatalogEntry]:
    tables = raw.get("tables") or {}
    vocab = _metric_vocabulary()
    out: list[CatalogEntry] = []
    for name, spec in tables.items():
        if not isinstance(spec, dict):
            continue
        cols = [str(c) for c in (spec.get("columns") or [])]
        gloss = _glossary_bits(raw, name)
        text = " ".join(
            [
                f"table {name}",
                "columns " + " ".join(cols),
                " ".join(gloss),
                # What people call this table when they ask for it.
                " ".join(vocab.get(name) or []),
            ]
        )
        out.append(
            CatalogEntry(
                table=name,
                text=text,
                columns=cols,
                embedding=text_embedding(text),
            )
        )
    return out


def _ensure_cache(path: Path = SEMANTIC_PATH) -> _Cache:
    global _CACHE
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    if _CACHE.entries and _CACHE.mtime_ns == mtime_ns:
        return _CACHE
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = _build_entries(raw)
    fingerprint = str(mtime_ns)
    _CACHE = _Cache(mtime_ns=mtime_ns, entries=entries, fingerprint=fingerprint)
    return _CACHE


def invalidate() -> None:
    global _CACHE
    _CACHE = _Cache()


def retrieve(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    path: Path = SEMANTIC_PATH,
) -> dict[str, Any]:
    """Return a reduced schema: top-k tables + columns + light join hints."""
    cache = _ensure_cache(path)
    q_emb = text_embedding(question or "")
    scored = sorted(
        (
            (cosine_similarity(q_emb, e.embedding), e)
            for e in cache.entries
        ),
        key=lambda t: t[0],
        reverse=True,
    )
    k = max(1, min(int(top_k), len(scored) or 1))
    chosen = [e for _, e in scored[:k]] if scored else []

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    chosen_names = {e.table for e in chosen}
    joins = []
    for j in raw.get("joins") or []:
        if not isinstance(j, dict):
            continue
        if j.get("from_table") in chosen_names and j.get("to_table") in chosen_names:
            joins.append(j)

    tables: dict[str, Any] = {}
    for e in chosen:
        tables[e.table] = {"columns": list(e.columns)}

    return {
        "tables": tables,
        "joins": joins,
        "catalog_fingerprint": cache.fingerprint,
        "top_k": [e.table for e in chosen],
    }


def schema_prompt_block(schema: dict[str, Any]) -> str:
    """Compact text for the LLM — reduced schema only."""
    lines = ["REDUCED SCHEMA (use ONLY these tables/columns):"]
    for name, spec in (schema.get("tables") or {}).items():
        cols = ", ".join(spec.get("columns") or [])
        lines.append(f"- {name}({cols})")
    if schema.get("joins"):
        lines.append("JOINS:")
        for j in schema["joins"]:
            lines.append(
                f"- {j.get('from_table')}.{j.get('from_column')} = "
                f"{j.get('to_table')}.{j.get('to_column')}"
            )
    return "\n".join(lines)


def cache_fingerprint() -> str:
    return _ensure_cache().fingerprint


__all__ = [
    "cache_fingerprint",
    "invalidate",
    "retrieve",
    "schema_prompt_block",
]
