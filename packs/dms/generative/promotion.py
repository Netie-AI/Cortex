"""L2 → L0 promotion signal (C7-full / arch §6.3).

Validated L2 answers used 5+ times surface for steward approval. Approval
appends a certified query entry. Frequency lives here until C8 ``query_run``
is durable; then this becomes a thin view over that table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "data" / "engine" / "l2_promotion.sqlite"
PROMOTE_AFTER = 5


def _connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS l2_usage (
            fingerprint TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            sql_text TEXT NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            surfaced INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    con.commit()
    return con


def fingerprint(question: str, sql: str) -> str:
    blob = (question.strip().lower() + "\n" + sql.strip()).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]


def record_validated(question: str, sql: str, *, path: Path = DEFAULT_DB) -> dict[str, Any]:
    fp = fingerprint(question, sql)
    now = datetime.now(timezone.utc).isoformat()
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT use_count, surfaced, approved FROM l2_usage WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO l2_usage (fingerprint, question, sql_text, use_count, last_used_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (fp, question, sql, now),
            )
            count = 1
            surfaced = 0
            approved = 0
        else:
            count = int(row["use_count"]) + 1
            surfaced = int(row["surfaced"])
            approved = int(row["approved"])
            con.execute(
                "UPDATE l2_usage SET use_count = ?, last_used_at = ?, question = ?, sql_text = ? "
                "WHERE fingerprint = ?",
                (count, now, question, sql, fp),
            )
        if count >= PROMOTE_AFTER and not surfaced and not approved:
            con.execute(
                "UPDATE l2_usage SET surfaced = 1 WHERE fingerprint = ?",
                (fp,),
            )
            surfaced = 1
        con.commit()
        return {
            "fingerprint": fp,
            "use_count": count,
            "surfaced": bool(surfaced),
            "approved": bool(approved),
            "ready_for_steward": count >= PROMOTE_AFTER and not approved,
        }
    finally:
        con.close()


def steward_queue(*, path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT fingerprint, question, sql_text, use_count, last_used_at "
            "FROM l2_usage WHERE use_count >= ? AND approved = 0 "
            "ORDER BY use_count DESC, last_used_at DESC",
            (PROMOTE_AFTER,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def approve(
    fp: str,
    *,
    verified_by: str = "steward",
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Mark approved and append to certified_queries.yaml (L0 library)."""
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT question, sql_text FROM l2_usage WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "unknown fingerprint"}
        con.execute(
            "UPDATE l2_usage SET approved = 1, surfaced = 1 WHERE fingerprint = ?",
            (fp,),
        )
        con.commit()
        question, sql_text = row["question"], row["sql_text"]
    finally:
        con.close()

    certified_path = ROOT / "packs" / "dms" / "semantic" / "certified_queries.yaml"
    try:
        import yaml

        raw = yaml.safe_load(certified_path.read_text(encoding="utf-8")) or {}
        queries = list(raw.get("queries") or raw.get("certified") or [])
        entry = {
            "id": f"l2_promoted_{fp}",
            "question": question,
            "sql": sql_text,
            "verified_by": verified_by,
            "verified_at": datetime.now(timezone.utc).date().isoformat(),
            "tags": ["l2_promoted"],
        }
        queries.append(entry)
        if "queries" in raw or not raw:
            raw = {"queries": queries}
        else:
            raw["certified"] = queries
        certified_path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        # Clear loader cache if present
        try:
            from packs.dms.semantic.loader import load_all

            load_all.cache_clear()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "entry": entry}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


__all__ = [
    "PROMOTE_AFTER",
    "approve",
    "fingerprint",
    "record_validated",
    "steward_queue",
]
