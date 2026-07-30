"""L2 — cleaning-rule PROPOSALS (governed, approval-gated).

Profiles a bronze table and proposes candidate expectations. Proposals are NEVER
auto-applied: they land in defs/proposed/ with status=pending and must be
approved by a steward before the runner will touch them. The profiler is
deterministic; an LLM may later enrich `propose()` behind the same gate, but the
approval requirement is the load-bearing governance control (LLM never mutates
data, never decides pass/fail).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import yaml

from packs.dms.lakehouse.catalog import LAKE_ALIAS, connect

PROPOSED_DIR = Path(__file__).resolve().parent / "defs" / "proposed"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def profile_source(source: str, *, con=None) -> dict[str, Any]:
    """Per-column null rate, distinct count, and numeric-parse success rate."""
    owns = con is None
    con = con or connect(read_only=True)
    src = f"{LAKE_ALIAS}.{source}" if "." in source else source
    try:
        total = int(con.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0])
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog=? AND table_schema=? AND table_name=? ORDER BY ordinal_position",
            [LAKE_ALIAS, *source.split(".", 1)]).fetchall()]
        profile: dict[str, Any] = {"source": source, "rows": total, "columns": {}}
        for c in cols:
            if c.startswith("_"):
                continue
            nulls = int(con.execute(
                f"SELECT COUNT(*) FROM {src} WHERE {c} IS NULL OR CAST({c} AS VARCHAR) = ''"
            ).fetchone()[0])
            numeric_ok = int(con.execute(
                f"SELECT COUNT(*) FROM {src} WHERE TRY_CAST({c} AS DOUBLE) IS NOT NULL"
            ).fetchone()[0])
            profile["columns"][c] = {
                "null_rate": round(nulls / total, 4) if total else 0.0,
                "numeric_rate": round(numeric_ok / total, 4) if total else 0.0,
            }
        return profile
    finally:
        if owns:
            con.close()


def propose(source: str, target: str, *, con=None) -> dict[str, Any]:
    """Deterministic candidate expectations from a profile. Saved as PENDING."""
    prof = profile_source(source, con=con)
    exps: list[dict] = []
    for col, stats in prof["columns"].items():
        # a column that is mostly-present but has some gaps → propose NOT NULL (warn)
        if 0.0 < stats["null_rate"] <= 0.2:
            exps.append({"name": f"{col}_present",
                         "constraint_sql": f"{col} IS NOT NULL", "action": "warn"})
        # a mostly-numeric column → propose non-negative (warn)
        if stats["numeric_rate"] >= 0.8:
            exps.append({"name": f"{col}_non_negative",
                         "constraint_sql": f"TRY_CAST({col} AS DOUBLE) >= 0", "action": "warn"})
    pid = f"proposed_{source.replace('.', '_')}"
    proposal = {
        "id": pid, "source": source, "target": target,
        # Profiler SELECT * does not invent provenance columns; document aggregate
        # until the source already carries T7 flat/_src columns (then switch to propagate).
        "lineage": "aggregate",
        "lineage_reason": (
            "profiler proposal copies source columns as-is; provenance propagation "
            "requires bronze T7 columns and an explicit propagate transform"
        ),
        "transform_sql": "SELECT * FROM {source}",
        "expectations": exps,
        "status": "pending", "created_at": _now(), "proposed_by": "profiler",
    }
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROPOSED_DIR / f"{pid}.yaml").write_text(
        yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
    return proposal


def list_proposals() -> list[dict[str, Any]]:
    if not PROPOSED_DIR.exists():
        return []
    out = []
    for p in sorted(PROPOSED_DIR.glob("*.yaml")):
        out.append(yaml.safe_load(p.read_text(encoding="utf-8")))
    return out


def get_proposal(pid: str) -> dict[str, Any] | None:
    path = PROPOSED_DIR / f"{pid}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def approve_proposal(pid: str, *, approver: str) -> dict[str, Any]:
    proposal = get_proposal(pid)
    if proposal is None:
        raise FileNotFoundError(f"no proposal {pid!r}")
    proposal["status"] = "approved"
    proposal["approved_by"] = approver
    proposal["approved_at"] = _now()
    (PROPOSED_DIR / f"{pid}.yaml").write_text(
        yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
    try:
        from packs.dms.audit import ledger

        ledger.append(approver, "pipeline.proposal_approved", {"proposal": pid})
    except Exception:  # noqa: BLE001
        pass
    return proposal


def run_if_approved(pid: str, *, actor: str = "system"):
    """Runner guard: a proposal must be approved before it can mutate data."""
    proposal = get_proposal(pid)
    if proposal is None:
        raise FileNotFoundError(f"no proposal {pid!r}")
    if proposal.get("status") != "approved":
        raise PermissionError(f"proposal {pid!r} is {proposal.get('status')!r}; approval required")
    from packs.dms.pipelines.runner import run_pipeline

    return run_pipeline(proposal, actor=actor)
