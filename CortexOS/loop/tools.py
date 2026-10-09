"""C-LOOP (#291) tool registry: chart spec and data map.

A tool is a pure function from plain data to a plain-data dict. The registry
runs every tool under :mod:`CortexOS.loop.sandbox` and stamps the run; a tool
never receives a connection, never reaches the network, and writes only to its
scratch folder.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CortexOS.loop.sandbox import SandboxRefused, plain_data, sandboxed

TOOL_UNKNOWN = "TOOL_UNKNOWN"
TOOL_ERROR = "TOOL_ERROR"
TOOL_BAD_OUTPUT = "TOOL_BAD_OUTPUT"

ToolFn = Callable[[Mapping[str, Any], Path], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolDef:
    id: str
    description: str
    fn: ToolFn


@dataclass(frozen=True, slots=True)
class ToolRun:
    tool: str
    served_status: str  # ok | failed | refused
    served_reason: str
    served_at: str
    output: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.served_status == "ok"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolRegistry:
    def __init__(self, tools: Sequence[ToolDef] = ()) -> None:
        self._tools: dict[str, ToolDef] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.id] = tool

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def get(self, tool_id: str) -> ToolDef | None:
        return self._tools.get(tool_id)

    def run(self, tool_id: str, inputs: Mapping[str, Any]) -> ToolRun:
        tool = self._tools.get(tool_id)
        if tool is None:
            return ToolRun(tool_id, "refused", f"{TOOL_UNKNOWN}: {tool_id!r} is not registered", _now())
        try:
            data = plain_data(dict(inputs))
        except SandboxRefused as exc:
            return ToolRun(tool_id, "refused", str(exc), _now())
        output: Any = None
        error = ""
        with sandboxed() as run:
            try:
                output = tool.fn(data, run.scratch)
            except SandboxRefused:
                pass  # recorded on the run
            except Exception as exc:  # noqa: BLE001 — a failed tool is a stamped step
                error = f"{TOOL_ERROR}: {type(exc).__name__}: {exc}"
        if run.refusals:
            return ToolRun(tool_id, "refused", str(run.refusals[0]), _now())
        if error:
            return ToolRun(tool_id, "failed", error, _now())
        try:
            clean = plain_data(output, where="output") if isinstance(output, Mapping) else None
        except SandboxRefused as exc:
            return ToolRun(tool_id, "failed", f"{TOOL_BAD_OUTPUT}: {exc}", _now())
        if clean is None:
            return ToolRun(tool_id, "failed", f"{TOOL_BAD_OUTPUT}: tool returned no mapping", _now())
        return ToolRun(tool_id, "ok", "", _now(), clean)


# -- chart spec ---------------------------------------------------------------
_TIME_HINT = re.compile(r"(date|time|day|week|month|quarter|year|period|_at$)", re.I)
CHART_MAX_POINTS = 20


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    return None


def chart_spec(inputs: Mapping[str, Any], scratch: Path) -> dict[str, Any]:
    """Bignum / bar / line spec from result rows. Generic: no table names assumed."""
    del scratch
    rows = [r for r in inputs.get("rows") or [] if isinstance(r, Mapping)]
    title = str(inputs.get("question") or "")[:80]
    if not rows:
        return {"type": "none", "reason": "no rows", "title": title, "data": []}
    columns = list(rows[0].keys())
    numeric = [c for c in columns if any(_number(r.get(c)) is not None for r in rows)]
    if len(rows) == 1 and len(columns) == 1 and numeric:
        col = columns[0]
        return {
            "type": "bignum",
            "value": _number(rows[0][col]),
            "label": col.replace("_", " ").upper(),
            "title": title,
            "data": [],
        }
    labels = [c for c in columns if c not in numeric]
    if not numeric:
        return {"type": "none", "reason": "no numeric column", "title": title, "data": []}
    value_key = numeric[-1]
    name_key = labels[0] if labels else next((c for c in columns if c != value_key), value_key)
    data = [
        {"name": str(r.get(name_key, "")), "value": v}
        for r in rows[:CHART_MAX_POINTS]
        if (v := _number(r.get(value_key))) is not None
    ]
    return {
        "type": "line" if _TIME_HINT.search(name_key) else "bar",
        "x_label": name_key,
        "y_label": value_key,
        "title": title,
        "data": data,
        "truncated": len(rows) > CHART_MAX_POINTS,
    }


# -- data map -----------------------------------------------------------------
def data_map(inputs: Mapping[str, Any], scratch: Path) -> dict[str, Any]:
    """Ontology graph of the tables this session may read.

    Nodes are the granted tables, with columns from the semantic layer and keys,
    joins and row counts from per-Space table memory. Edges are joins whose two
    ends are both granted. Nothing ungranted appears.
    """
    del scratch
    granted = sorted({str(t).lower() for t in inputs.get("granted") or []})
    schema = inputs.get("schema") or {}
    tables = {str(k).lower(): v for k, v in (schema.get("tables") or {}).items()}
    nodes: dict[str, dict[str, Any]] = {}
    for name in granted:
        spec = tables.get(name) or {}
        nodes[name] = {
            "table": name,
            "columns": list(spec.get("columns") or []),
            "keys": [],
            "row_count": None,
            "sources": ["semantic_layer"] if name in tables else [],
            "memory_ids": [],
        }
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _edge(a: str, ac: str, b: str, bc: str, source: str) -> None:
        a, b = a.lower(), b.lower()
        key = (a, ac, b, bc)
        if a in nodes and b in nodes and key not in seen:
            seen.add(key)
            edges.append({"from": a, "from_column": ac, "to": b, "to_column": bc, "source": source})

    for j in schema.get("joins") or []:
        _edge(
            str(j.get("from_table", "")),
            str(j.get("from_column", "")),
            str(j.get("to_table", "")),
            str(j.get("to_column", "")),
            "semantic_layer",
        )
    for entry in inputs.get("table_memory") or []:
        body = entry.get("body") or {}
        name = str(body.get("table") or entry.get("key") or "").lower()
        if name not in nodes:
            continue
        node = nodes[name]
        node["memory_ids"].append(str(entry.get("id")))
        node["sources"].append("space_memory")
        for col in body.get("columns") or []:
            if col not in node["columns"]:
                node["columns"].append(col)
        node["keys"] = list(body.get("keys") or node["keys"])
        if isinstance(body.get("row_count"), int):
            node["row_count"] = body["row_count"]
        for j in body.get("joins") or []:
            _edge(name, str(j.get("on") or j.get("from_column") or ""), str(j.get("to", "")),
                  str(j.get("to_column") or j.get("on") or ""), "space_memory")
    return {"nodes": [nodes[n] for n in granted], "edges": edges}


CHART_SPEC = ToolDef("chart_spec", "Chart spec (bignum/bar/line) from checked result rows", chart_spec)
DATA_MAP = ToolDef("data_map", "Ontology graph of granted tables, joins and table memory", data_map)


def default_registry() -> ToolRegistry:
    return ToolRegistry((CHART_SPEC, DATA_MAP))
