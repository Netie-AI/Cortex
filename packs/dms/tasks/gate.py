"""F5 compliance gate — deterministic task verdict before execution."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from CortexOS.compliance.engine import ComplianceEngine, RuleViolation
from packs.dms.audit.ledger import append as ledger_append
from packs.dms.audit.ledger import default_db_path, init_ledger_schema
from packs.dms.security.pii import redact_for_prompt

RULESET = Path(__file__).resolve().parents[1] / "compliance" / "dms_rules_v1.yaml"
VALUE_THRESHOLD_MYR = float(os.getenv("DMS_VALUE_THRESHOLD_MYR", "5000"))


@dataclass(frozen=True, slots=True)
class ComplianceVerdict:
    status: Literal["pass", "warn", "fail"]
    violations: list[dict[str, Any]]
    executable: bool
    event_id: str | None = None


def _resolve_db(db_path: Path | str | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return default_db_path()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_task_events_schema(con: sqlite3.Connection) -> None:
    init_ledger_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dms_task_events (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            thread_id TEXT,
            task_id TEXT NOT NULL,
            intent TEXT,
            filled_template TEXT NOT NULL DEFAULT '{}',
            gate_status TEXT,
            violations TEXT NOT NULL DEFAULT '[]',
            executable INTEGER NOT NULL DEFAULT 0,
            human_acknowledged INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dms_task_events_thread
            ON dms_task_events(thread_id, created_at);
        """
    )
    con.commit()


def _redact_template(template: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in template.items():
        if isinstance(value, str):
            out[key] = redact_for_prompt(value)
        else:
            out[key] = value
    return out


def _violations_to_dicts(violations: list[RuleViolation]) -> list[dict[str, Any]]:
    return [
        {"rule_id": v.rule_id, "severity": v.severity, "message": v.message}
        for v in violations
    ]


def _apply_value_threshold(extracted: dict[str, Any], violations: list[RuleViolation]) -> list[RuleViolation]:
    value = extracted.get("value_myr", 0)
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        value_f = 0.0
    if value_f > VALUE_THRESHOLD_MYR and not extracted.get("human_acknowledged"):
        violations = list(violations)
        violations.append(
            RuleViolation(
                rule_id="value_threshold_requires_human",
                severity="warning",
                message=f"Task value {value_f} MYR exceeds threshold {VALUE_THRESHOLD_MYR}",
            )
        )
    return violations


def _status_from_violations(violations: list[RuleViolation], extracted: dict[str, Any]) -> tuple[str, bool]:
    if any(v.severity == "error" for v in violations):
        return "fail", False
    if any(v.severity == "warning" for v in violations):
        ack = bool(extracted.get("human_acknowledged"))
        return "warn", ack
    return "pass", True


def evaluate_template(filled_template: dict[str, Any]) -> ComplianceVerdict:
    safe = _redact_template(filled_template)
    extracted = {"doc_type": "dms_task", **safe}
    engine = ComplianceEngine(str(RULESET))
    violations = engine.evaluate("dms_task", extracted)
    violations = _apply_value_threshold(extracted, violations)
    status, executable = _status_from_violations(violations, extracted)
    return ComplianceVerdict(
        status=status,  # type: ignore[arg-type]
        violations=_violations_to_dicts(violations),
        executable=executable,
    )


def create_task_event(
    *,
    message_id: str | None,
    thread_id: str | None,
    task_id: str,
    intent: str | None,
    filled_template: dict[str, Any],
    actor: str = "system",
    db_path: Path | str | None = None,
) -> str:
    path = _resolve_db(db_path)
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    con = _connect(path)
    try:
        init_task_events_schema(con)
        con.execute(
            """
            INSERT INTO dms_task_events
            (id, message_id, thread_id, task_id, intent, filled_template,
             gate_status, violations, executable, human_acknowledged, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, '[]', 0, ?, ?)
            """,
            (
                event_id,
                message_id,
                thread_id,
                task_id,
                intent,
                json.dumps(filled_template),
                int(bool(filled_template.get("human_acknowledged"))),
                now,
            ),
        )
        con.commit()
    finally:
        con.close()

    ledger_append(
        actor,
        "task.event_created",
        {"event_id": event_id, "task_id": task_id, "intent": intent},
        db_path=path,
    )
    return event_id


def check_task(
    event_id: str,
    task_id: str,
    filled_template: dict[str, Any],
    *,
    raw_text: str | None = None,
    actor: str = "system",
    db_path: Path | str | None = None,
) -> ComplianceVerdict:
    from packs.dms.tasks import extract

    path = _resolve_db(db_path)
    merged = dict(filled_template)
    if raw_text:
        extracted_fields = extract.extract_fields(raw_text, task_id)
        merged.update({k: v for k, v in extracted_fields.items() if k not in ("verdict", "force_pass")})

    verdict = evaluate_template(merged)

    ledger_event = {
        "pass": "task.gate_passed",
        "warn": "task.gate_warned",
        "fail": "task.gate_failed",
    }[verdict.status]

    con = _connect(path)
    try:
        init_task_events_schema(con)
        con.execute(
            """
            UPDATE dms_task_events
            SET gate_status = ?, violations = ?, executable = ?,
                human_acknowledged = ?, filled_template = ?
            WHERE id = ?
            """,
            (
                verdict.status,
                json.dumps(verdict.violations),
                int(verdict.executable),
                int(bool(merged.get("human_acknowledged"))),
                json.dumps(merged),
                event_id,
            ),
        )
        con.commit()
    finally:
        con.close()

    ledger_append(
        actor,
        ledger_event,
        {
            "event_id": event_id,
            "task_id": task_id,
            "status": verdict.status,
            "violations": verdict.violations,
            "executable": verdict.executable,
        },
        db_path=path,
    )

    return ComplianceVerdict(
        status=verdict.status,
        violations=verdict.violations,
        executable=verdict.executable,
        event_id=event_id,
    )


def acknowledge_event(
    event_id: str,
    *,
    actor: str = "steward",
    db_path: Path | str | None = None,
) -> ComplianceVerdict:
    path = _resolve_db(db_path)
    con = _connect(path)
    try:
        init_task_events_schema(con)
        row = con.execute(
            "SELECT task_id, filled_template FROM dms_task_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"task event not found: {event_id}")

    template = json.loads(row["filled_template"])
    template["human_acknowledged"] = True
    return check_task(event_id, row["task_id"], template, actor=actor, db_path=path)
