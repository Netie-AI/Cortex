"""F8 sandboxed tool-call runner — allowlist, sanitize, compliance, actor outputs."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-.]{1,128}$")
_RULESET = "packs/dms/compliance/tool_call_rules_v1.yaml"

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / "outputs"


def allowed_action_tools(db_path: Path | str | None = None) -> frozenset[str]:
    """Tool ids the F8 runner may execute — sourced from the governed ontology
    action-type registry (``kind: tool``), never a hardcoded set (O3 = F8).

    Reads the compiled ``ontology_action_types`` table in the ops DB when present,
    and falls back to the pack's ``action_types.yaml`` so the allowlist is always
    registry-derived. Adding a tool = registering an action type, not editing code.
    """
    from packs.dms.audit.ledger import default_db_path

    path = Path(db_path) if db_path is not None else default_db_path()
    if path.exists():
        try:
            conn = sqlite3.connect(path)
            try:
                rows = conn.execute(
                    "SELECT id FROM ontology_action_types WHERE kind = 'tool'"
                ).fetchall()
            finally:
                conn.close()
            if rows:
                return frozenset(str(r[0]) for r in rows)
        except sqlite3.Error:
            pass  # table absent / DB not compiled — fall through to the YAML source

    from packs.dms.ontology.registry import load_action_types

    return frozenset(a.id for a in load_action_types() if a.kind == "tool")


class ToolCallError(RuntimeError):
    """Raised when a tool call is denied or fails after ledger write."""

    def __init__(self, message: str, *, verdict: str = "deny", detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.verdict = verdict
        self.detail = detail or {}


def params_hash(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    from packs.dms.security.injection_guard import sanitize_for_prompt
    from packs.dms.security.pii import redact_for_prompt

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            cleaned, _ = sanitize_for_prompt(value)
            return redact_for_prompt(cleaned)
        if isinstance(value, dict):
            return {str(k): _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    return _walk(dict(params or {}))


def resolve_output_dir(actor: str, run_id: str) -> Path:
    """Return outputs/<actor>/<run_id>/ or raise ToolCallError on path escape."""
    if not _SAFE_SEGMENT.match(actor or "") or not _SAFE_SEGMENT.match(run_id or ""):
        raise ToolCallError(
            "path escape denied: actor/run_id must be safe path segments",
            verdict="path_escape",
        )
    root = OUTPUTS.resolve()
    out = (OUTPUTS / actor / run_id).resolve()
    try:
        out.relative_to(root)
    except ValueError as exc:
        raise ToolCallError(
            "path escape denied: output outside outputs/",
            verdict="path_escape",
        ) from exc
    return out


def _compliance_check(params: dict[str, Any]) -> list[str]:
    from netie.compliance.engine import ComplianceEngine

    engine = ComplianceEngine.from_ruleset(_RULESET)
    return engine.check({"doc_type": "tool_call", "extracted": params})


def _ledger(
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    db_path: Path | str | None = None,
) -> None:
    from packs.dms.audit import ledger

    ledger.append(actor, event_type, payload, db_path=db_path)


def _run_export_pptx(params: dict[str, Any], out_dir: Path) -> Path:
    from packs.dms.actions.export_pptx import write_export_pptx

    title = str(params.get("title") or "")
    body = str(params.get("body") or params.get("subtitle") or "")
    path = out_dir / "export.pptx"
    return write_export_pptx(path, title=title, body=body)


def run_tool_call(
    tool: str,
    params: dict[str, Any] | None = None,
    *,
    actor: str,
    run_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Execute an allowlisted tool under outputs/<actor>/<run_id>/.

    Always appends ``action.tool_call`` or ``action.tool_call_denied`` to the F1 ledger.
    """
    raw_params = dict(params or {})
    phash = params_hash(raw_params)
    rid = run_id or uuid.uuid4().hex[:12]
    tool_name = (tool or "").strip()

    def _deny(verdict: str, reason: str, path: str | None = None) -> dict[str, Any]:
        payload = {
            "tool": tool_name,
            "params_hash": phash,
            "path": path,
            "verdict": verdict,
            "reason": reason,
            "run_id": rid,
        }
        _ledger(actor, "action.tool_call_denied", payload, db_path=db_path)
        raise ToolCallError(reason, verdict=verdict, detail=payload)

    if tool_name not in allowed_action_tools(db_path):
        _deny("allowlist", f"tool {tool_name!r} not a registered action type")

    try:
        out_dir = resolve_output_dir(actor, rid)
    except ToolCallError as exc:
        _deny(exc.verdict, str(exc))

    clean = sanitize_params(raw_params)
    violations = _compliance_check(clean)
    if violations:
        _deny("compliance_fail", f"compliance violations: {violations}")

    out_dir.mkdir(parents=True, exist_ok=True)
    if tool_name == "export_pptx":
        path = _run_export_pptx(clean, out_dir)
    else:  # pragma: no cover — allowlist already filtered
        _deny("allowlist", f"tool {tool_name!r} has no handler")

    # Canonical relative path for ledger (stable even when OUTPUTS is redirected in tests).
    rel = f"outputs/{actor}/{rid}/{path.name}"
    payload = {
        "tool": tool_name,
        "params_hash": phash,
        "path": rel,
        "verdict": "pass",
        "run_id": rid,
    }
    _ledger(actor, "action.tool_call", payload, db_path=db_path)
    return {
        "ok": True,
        "tool": tool_name,
        "path": payload["path"],
        "run_id": rid,
        "verdict": "pass",
        "params_hash": phash,
    }
