"""Governed step trace for POST /v1/insights.

Built only from events this request actually ran. No model call. The trace
is omitted when it was not requested or ``schema_context`` is absent, so
those envelopes stay byte-equal.

``shortlist`` is read as the structured field #351 is adding on
``InsightsWireIn``. This module is the stand-in until that field lands.
It does not parse ``reason=`` tokens out of the schema string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from CortexOS.insights import schema_context as schema_mod

# Engine-local sample mask. CortexOS must not import packs.*, so this does
# not call the DMS prompt redactor. Same classes: nric, card, email, phone.
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{1,4}\b|\b\d{13,19}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}(?!\d)"
)
_PII: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nric", _NRIC),
    ("credit_card", _CARD),
    ("email", _EMAIL),
    ("phone", _PHONE),
)

# A table line is "- <name>" with an optional reason= token that is ignored.
_TABLE_LINE = re.compile(
    r"^\s*-\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:reason=\S+\s*)?$"
)
_SCHEMA_HEADS = frozenset({"SCHEMA", "TABLES"})
_JOIN_HEADS = frozenset({"JOINS", "JOIN"})

_slot: ContextVar[dict[str, Any] | None] = ContextVar("insights_step_trace", default=None)


def active() -> bool:
    return _slot.get() is not None


def empty_reason(value: Any) -> str:
    """Null and non-strings are an explicit empty reason. Never invented."""
    if isinstance(value, str):
        return value
    return ""


def tables_from_schema(text: str) -> list[str]:
    """Table names written in the schema string. Reasons are not read."""
    names: list[str] = []
    seen: set[str] = set()
    in_schema = False
    saw_schema = False
    for line in (text or "").splitlines():
        head = _head(line)
        if head in _SCHEMA_HEADS:
            in_schema = True
            saw_schema = True
            continue
        if head in _JOIN_HEADS:
            if saw_schema:
                break
            in_schema = False
            continue
        if saw_schema and not in_schema:
            continue
        match = _TABLE_LINE.match(line)
        if match is None:
            continue
        name = match.group(1)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def shortlist_picks(shortlist: Any, schema_text: str | None) -> list[dict[str, Any]]:
    """Step 1 picks. A sent list wins. Otherwise tables, each reason empty."""
    if isinstance(shortlist, list):
        picks: list[dict[str, Any]] = []
        for item in shortlist:
            entry = _entry(item)
            if entry is not None:
                picks.append(entry)
        return picks
    return [_table_only(name) for name in tables_from_schema(schema_text or "")]


def begin(shortlist: Any, schema_text: str | None) -> None:
    """Start a trace and record step 1. No-op callers leave the slot empty."""
    state: dict[str, Any] = {"steps": [], "thinks": 0, "generates": 0}
    _slot.set(state)
    _append(
        "shortlist",
        "ok",
        None,
        {},
        shortlist=shortlist_picks(shortlist, schema_text),
    )


def clear() -> None:
    _slot.set(None)


def attach(out: dict[str, Any]) -> None:
    """Add ``steps`` only when this request opened a trace."""
    state = _slot.get()
    if state is None:
        return
    out["steps"] = list(state["steps"])


def note_model(purpose: str, out: Mapping[str, Any]) -> None:
    """One step per FreeRoute complete() that returned. First of each kind, then retries."""
    state = _slot.get()
    if state is None:
        return
    kind = _model_kind(state, purpose or "")
    ok = bool(out.get("ok"))
    refusal = None
    if not ok:
        refusal = str(out.get("refused") or out.get("refuse_reason") or "refused")
    _append(kind, "ok" if ok else "REFUSE", refusal, _stamp(out.get("stamp")))


def note_check(*, ok: bool, refusal: str | None, stamp: Any = None) -> None:
    state = _slot.get()
    if state is None:
        return
    named = None if ok else (refusal or "")
    _append("check", "ok" if ok else "REFUSE", named, _stamp(stamp))


def note_refuse(reason: str, stamp: Any = None) -> None:
    state = _slot.get()
    if state is None:
        return
    _append("refuse", "REFUSE", str(reason or ""), _stamp(stamp))


def note_execute(sql: str, rows: Any, *, ok: bool = True, stamp: Any = None) -> None:
    """Store ``sql`` unchanged. Mask and cap the row sample."""
    state = _slot.get()
    if state is None or not isinstance(sql, str) or sql == "":
        return
    raw = list(rows) if isinstance(rows, list) else []
    sample, cap, truncated = sample_rows(raw)
    _append(
        "execute",
        "ok" if ok else "REFUSE",
        None,
        _stamp(stamp),
        sql=sql,
        rows=mask_rows(sample),
        row_cap=cap,
        truncated=truncated,
    )


def sample_rows(rows: list[Any]) -> tuple[list[Any], int, bool]:
    """Slice to the schema_context row cap. Stamp values are the cap and the flag."""
    cap = schema_mod.row_cap()
    truncated = len(rows) > cap
    return list(rows[:cap]), cap, truncated


def mask_rows(rows: list[Any]) -> list[Any]:
    return [_mask_cell(row) for row in rows]


def mask_text(text: str) -> str:
    spans: list[tuple[int, int, str]] = []
    for kind, pattern in _PII:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), kind))
    if not spans:
        return text
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    parts: list[str] = []
    cursor = 0
    for start, end, kind in spans:
        if start < cursor:
            continue
        parts.append(text[cursor:start])
        parts.append(f"[REDACTED:{kind}]")
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _mask_cell(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {key: _mask_cell(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_cell(item) for item in value]
    return value


def _head(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    return stripped.split()[0].rstrip(":").upper()


def _table_only(name: str) -> dict[str, Any]:
    return {
        "table": name,
        "reason": empty_reason(None),
        "columns": [],
        "joins": [],
    }


def _entry(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    table = item.get("table")
    if not isinstance(table, str) or not table.strip():
        return None
    columns: list[dict[str, Any]] = []
    raw_columns = item.get("columns")
    if isinstance(raw_columns, list):
        for col in raw_columns:
            if not isinstance(col, dict):
                continue
            name = col.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            columns.append({"name": name, "reason": empty_reason(col.get("reason"))})
    joins: list[dict[str, Any]] = []
    raw_joins = item.get("joins")
    if isinstance(raw_joins, list):
        for join in raw_joins:
            if not isinstance(join, dict):
                continue
            left = join.get("left")
            right = join.get("right")
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            on = join.get("on")
            joins.append(
                {
                    "left": left,
                    "right": right,
                    "on": on if isinstance(on, str) else "",
                    "reason": empty_reason(join.get("reason")),
                }
            )
    return {
        "table": table,
        "reason": empty_reason(item.get("reason")),
        "columns": columns,
        "joins": joins,
    }


def _model_kind(state: dict[str, Any], purpose: str) -> str:
    if purpose == "think":
        state["thinks"] = int(state["thinks"]) + 1
        return "think" if state["thinks"] == 1 else "retry"
    if purpose == "generative_ask":
        state["generates"] = int(state["generates"]) + 1
        return "generate" if state["generates"] == 1 else "retry"
    return "retry"


def _stamp(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _append(kind: str, status: str, refusal: str | None, stamps: dict[str, Any], **extra: Any) -> None:
    state = _slot.get()
    if state is None:
        return
    step: dict[str, Any] = {
        "n": len(state["steps"]) + 1,
        "kind": kind,
        "status": status,
        "refusal": refusal,
        "stamps": stamps,
    }
    step.update(extra)
    state["steps"].append(step)
