"""L2 — declarative pipeline runner with data-quality expectations.

A pipeline transforms a source table into a target table, gated by expectations:
  * action=fail : any violation aborts the whole run (target untouched) + ledger.
  * action=drop : violating rows go to <target>_quarantine with a reason; the rest
                  land in the target. Reconciles: rows_in == rows_out + rows_dropped.
  * action=warn : violations are counted and logged, rows kept.
Every run writes a metrics row to lake.bronze._pipeline_events (the DLT event-log
analog) and an F1 audit event.

The engine is deterministic; the LLM proposes rules (propose.py) but never decides
pass/fail and never mutates data directly.
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from packs.dms.lakehouse.catalog import LAKE_ALIAS, SCHEMAS, connect

ROOT = Path(__file__).resolve().parents[3]
DEFS_DIR = ROOT / "packs" / "dms" / "pipelines" / "defs"
EVENTS_TABLE = f"{LAKE_ALIAS}.bronze._pipeline_events"

_VALID_ACTIONS = ("warn", "drop", "fail")
_VALID_LINEAGE = ("propagate", "aggregate")
# Demo-core flat columns (DMS T7) or Appendix A STRUCT.
_PROVENANCE_FLAT = frozenset({"_src_ref_id", "_src_row", "_ingest_id"})
_PROVENANCE_STRUCT = frozenset({"_src", "_ingest_id"})


class PipelineError(RuntimeError):
    pass


@dataclass(slots=True)
class ExpectationResult:
    name: str
    action: str
    violations: int


@dataclass(slots=True)
class PipelineRun:
    run_id: str
    pipeline_id: str
    status: str                       # completed | failed
    rows_in: int = 0
    rows_out: int = 0
    rows_dropped: int = 0
    warn_count: int = 0
    expectations: list[ExpectationResult] = field(default_factory=list)
    quarantine_table: str | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "pipeline_id": self.pipeline_id, "status": self.status,
            "rows_in": self.rows_in, "rows_out": self.rows_out,
            "rows_dropped": self.rows_dropped, "warn_count": self.warn_count,
            "quarantine_table": self.quarantine_table, "error": self.error,
            "expectations": [
                {"name": e.name, "action": e.action, "violations": e.violations}
                for e in self.expectations
            ],
        }


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def load_pipeline(name_or_path: str | Path) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.exists():
        path = DEFS_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        raise PipelineError(f"pipeline def not found: {name_or_path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _validate_def(pdef: dict) -> None:
    for key in ("id", "source", "target", "transform_sql"):
        if not pdef.get(key):
            raise PipelineError(f"pipeline def missing {key!r}")
    schema, _, name = str(pdef["target"]).partition(".")
    if schema not in SCHEMAS or not name:
        raise PipelineError(f"target must be <schema>.<name> with schema in {SCHEMAS}")
    lineage = pdef.get("lineage")
    if lineage not in _VALID_LINEAGE:
        raise PipelineError(
            f"pipeline def requires lineage: propagate|aggregate (got {lineage!r})"
        )
    if lineage == "aggregate" and not str(pdef.get("lineage_reason") or "").strip():
        raise PipelineError(
            "lineage: aggregate requires a non-empty lineage_reason "
            "(architecture §4.4 — document why _src is not propagated)"
        )
    for exp in pdef.get("expectations") or []:
        if not exp.get("name") or not exp.get("constraint_sql"):
            raise PipelineError(f"expectation needs name + constraint_sql: {exp}")
        if exp.get("action", "warn") not in _VALID_ACTIONS:
            raise PipelineError(f"bad action {exp.get('action')!r} in {exp['name']}")


def _target_has_provenance(con, target: str) -> bool:
    """True if target carries flat T7 cols or STRUCT _src + _ingest_id."""
    try:
        cols = {str(r[0]).lower() for r in con.execute(f"DESCRIBE {target}").fetchall()}
    except Exception:  # noqa: BLE001
        return False
    if _PROVENANCE_FLAT <= cols:
        return True
    return _PROVENANCE_STRUCT <= cols


def _ensure_events(con) -> None:
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {LAKE_ALIAS}.bronze")
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} ("
        " run_id VARCHAR, pipeline_id VARCHAR, ts VARCHAR, status VARCHAR,"
        " rows_in BIGINT, rows_out BIGINT, rows_dropped BIGINT, warn_count BIGINT,"
        " detail VARCHAR)"
    )


def _valid_expr(constraints: list[str]) -> str:
    # A row is valid for a constraint only when it evaluates TRUE (NULL = invalid).
    if not constraints:
        return "TRUE"
    return " AND ".join(f"COALESCE(({c}), FALSE)" for c in constraints)


def _reason_case(drop_exps: list[dict]) -> str:
    whens = " ".join(
        f"WHEN NOT COALESCE(({e['constraint_sql']}), FALSE) THEN '{e['name']}'"
        for e in drop_exps
    )
    return f"CASE {whens} ELSE 'unknown' END"


def _audit(event: str, payload: dict, *, actor: str) -> None:
    try:
        from packs.dms.audit import ledger

        ledger.append(actor, event, payload)
    except Exception:  # noqa: BLE001
        pass


def run_pipeline(pdef: dict | str | Path, *, con=None, actor: str = "system") -> PipelineRun:
    if not isinstance(pdef, dict):
        pdef = load_pipeline(pdef)
    _validate_def(pdef)

    pid = pdef["id"]
    run_id = uuid.uuid4().hex[:12]
    source = f"{LAKE_ALIAS}.{pdef['source']}" if "." in pdef["source"] else pdef["source"]
    tgt_schema, _, tgt_name = str(pdef["target"]).partition(".")
    target = f"{LAKE_ALIAS}.{tgt_schema}.{tgt_name}"
    quarantine = f"{LAKE_ALIAS}.{tgt_schema}.{tgt_name}_quarantine"
    exps = pdef.get("expectations") or []
    fail_exps = [e for e in exps if e.get("action") == "fail"]
    drop_exps = [e for e in exps if e.get("action") == "drop"]
    warn_exps = [e for e in exps if e.get("action", "warn") == "warn"]

    owns = con is None
    con = con or connect()
    stg = f"_stg_{run_id}"
    run = PipelineRun(run_id=run_id, pipeline_id=pid, status="completed")
    try:
        _ensure_events(con)
        transform = str(pdef["transform_sql"]).replace("{source}", source)
        con.execute(f"CREATE OR REPLACE TEMP TABLE {stg} AS {transform}")
        run.rows_in = int(con.execute(f"SELECT COUNT(*) FROM {stg}").fetchone()[0])

        # fail expectations abort before anything is written
        for e in fail_exps:
            bad = int(con.execute(
                f"SELECT COUNT(*) FROM {stg} WHERE NOT COALESCE(({e['constraint_sql']}), FALSE)"
            ).fetchone()[0])
            run.expectations.append(ExpectationResult(e["name"], "fail", bad))
            if bad > 0:
                run.status = "failed"
                run.error = f"fail-expectation {e['name']!r} violated by {bad} rows"
                _record_event(con, run)
                _audit("pipeline.failed",
                       {"pipeline": pid, "run_id": run_id, "expectation": e["name"], "violations": bad},
                       actor=actor)
                return run

        valid = _valid_expr([e["constraint_sql"] for e in drop_exps])
        # quarantine the droppable rows with a reason
        if drop_exps:
            con.execute(
                f"CREATE OR REPLACE TABLE {quarantine} AS "
                f"SELECT *, {_reason_case(drop_exps)} AS _quarantine_reason, "
                f"'{_now()}' AS _quarantined_at FROM {stg} WHERE NOT ({valid})"
            )
            run.rows_dropped = int(con.execute(f"SELECT COUNT(*) FROM {quarantine}").fetchone()[0])
            run.quarantine_table = f"{tgt_schema}.{tgt_name}_quarantine"
            for e in drop_exps:
                v = int(con.execute(
                    f"SELECT COUNT(*) FROM {stg} WHERE NOT COALESCE(({e['constraint_sql']}), FALSE)"
                ).fetchone()[0])
                run.expectations.append(ExpectationResult(e["name"], "drop", v))

        # write survivors to the target
        con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {stg} WHERE {valid}")
        run.rows_out = int(con.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0])

        # warn expectations (counted on survivors, rows kept)
        for e in warn_exps:
            v = int(con.execute(
                f"SELECT COUNT(*) FROM {target} WHERE NOT COALESCE(({e['constraint_sql']}), FALSE)"
            ).fetchone()[0])
            run.expectations.append(ExpectationResult(e["name"], "warn", v))
            run.warn_count += v

        if run.rows_in != run.rows_out + run.rows_dropped:
            raise PipelineError(
                f"reconciliation failed: in={run.rows_in} out={run.rows_out} dropped={run.rows_dropped}")

        # T7 / §4.4: propagate must leave provenance columns on silver
        if pdef.get("lineage") == "propagate" and not _target_has_provenance(con, target):
            run.status = "failed"
            run.error = (
                "lineage:propagate requires target columns "
                "_src_ref_id+_src_row+_ingest_id (or _src+_ingest_id); "
                "got none — fix transform_sql or use lineage:aggregate with a reason"
            )
            _record_event(con, run)
            _audit(
                "pipeline.failed",
                {"pipeline": pid, "run_id": run_id, "error": run.error},
                actor=actor,
            )
            return run

        _record_event(con, run)
        _audit("pipeline.completed", run.to_dict(), actor=actor)
        return run
    finally:
        try:
            con.execute(f"DROP TABLE IF EXISTS {stg}")
        except Exception:  # noqa: BLE001
            pass
        if owns:
            con.close()


def _record_event(con, run: PipelineRun) -> None:
    _ensure_events(con)
    con.execute(
        f"INSERT INTO {EVENTS_TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
        [run.run_id, run.pipeline_id, _now(), run.status, run.rows_in, run.rows_out,
         run.rows_dropped, run.warn_count, json.dumps(run.to_dict())],
    )


def pipeline_events(*, con=None, limit: int = 100) -> list[dict[str, Any]]:
    owns = con is None
    con = con or connect(read_only=True)
    try:
        rel = con.execute(f"SELECT * FROM {EVENTS_TABLE} ORDER BY ts DESC LIMIT {int(limit)}")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=False)) for row in rel.fetchall()]
    except Exception:  # noqa: BLE001 — no events yet
        return []
    finally:
        if owns:
            con.close()
