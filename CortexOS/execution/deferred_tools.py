"""Deferred tool catalog — names-only until explicit inject (Claude Code ToolSearch pattern).

distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md
  "Deferred tool loading is default-on; ToolSearch injects schemas mid-turn"

Policy: keep full schemas out of the prompt until a search/inject step.
Name-only entries cost ~120 tokens each upstream; we approximate with a
cheap char-based estimate for Netie pack/MCP manifests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+./:-]*", re.I)


@dataclass(slots=True)
class ToolNameEntry:
    name: str
    kind: str = "tool"  # tool | mcp | skill | action
    description_short: str = ""
    source: str = ""
    estimated_schema_tokens: int = 0


@dataclass
class DeferredToolCatalog:
    """In-memory deferred catalog with inject log (mirrors deferred_tools_delta)."""

    entries: dict[str, ToolNameEntry] = field(default_factory=dict)
    injected: dict[str, dict[str, Any]] = field(default_factory=dict)
    inject_log: list[dict[str, Any]] = field(default_factory=list)

    def register(self, entry: ToolNameEntry) -> None:
        self.entries[entry.name] = entry

    def register_many(self, items: Iterable[ToolNameEntry]) -> None:
        for it in items:
            self.register(it)

    def names_only(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        out = [
            {
                "name": e.name,
                "kind": e.kind,
                "description": e.description_short[:160],
                "source": e.source,
                "deferred": e.name not in self.injected,
                "estimated_schema_tokens": e.estimated_schema_tokens
                or estimate_schema_tokens(e.description_short),
            }
            for e in self.entries.values()
        ]
        out.sort(key=lambda x: x["name"])
        return out[:limit] if limit else out

    def search(self, query: str, *, top_k: int = 8) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return self.names_only(limit=top_k)
        tokens = set(_TOKEN.findall(q))
        scored: list[tuple[float, ToolNameEntry]] = []
        for e in self.entries.values():
            blob = f"{e.name} {e.description_short} {e.kind} {e.source}".lower()
            score = 0.0
            if q in blob:
                score += 2.0
            score += sum(1.0 for t in tokens if t in blob)
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = []
        for score, e in scored[:top_k]:
            hits.append(
                {
                    "name": e.name,
                    "kind": e.kind,
                    "score": round(score, 3),
                    "description": e.description_short[:240],
                    "deferred": e.name not in self.injected,
                    "source": e.source,
                }
            )
        return hits

    def inject(self, name: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Materialize full schema for one tool. Raises KeyError if unknown."""
        if name not in self.entries:
            raise KeyError(f"unknown deferred tool {name!r} — search first")
        entry = self.entries[name]
        payload = schema or {
            "name": entry.name,
            "kind": entry.kind,
            "description": entry.description_short,
            "source": entry.source,
            "inputSchema": {"type": "object", "properties": {}},
        }
        self.injected[name] = payload
        event = {"event": "deferred_tools_delta", "injected": [name], "schema_keys": list(payload)}
        self.inject_log.append(event)
        return {"ok": True, **event, "schema": payload}

    def require_injected(self, name: str) -> dict[str, Any]:
        if name not in self.injected:
            raise PermissionError(
                f"tool {name!r} schema not injected — call tool_search/inject first "
                "(deferred catalog policy)"
            )
        return self.injected[name]

    def projected_cost_tokens(self, *, injected_only: bool = False) -> int:
        total = 0
        for name, e in self.entries.items():
            if injected_only and name not in self.injected:
                total += 8  # name-only stub
                continue
            if name in self.injected:
                total += e.estimated_schema_tokens or estimate_schema_tokens(
                    str(self.injected[name])
                )
            else:
                total += 8
        return total


def estimate_schema_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for capability manifests."""
    return max(8, len(text or "") // 4)


def catalog_from_mcp_tools(tools: list[dict[str, Any]]) -> DeferredToolCatalog:
    cat = DeferredToolCatalog()
    for t in tools:
        name = str(t.get("name") or "")
        if not name:
            continue
        desc = str(t.get("description") or "")
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        est = estimate_schema_tokens(desc + str(schema))
        cat.register(
            ToolNameEntry(
                name=name,
                kind="mcp",
                description_short=desc,
                source="mcp_catalog",
                estimated_schema_tokens=est,
            )
        )
    return cat


def catalog_from_discovery_hits(hits: list[dict[str, Any]]) -> DeferredToolCatalog:
    cat = DeferredToolCatalog()
    for h in hits:
        name = str(h.get("name") or h.get("id") or "")
        if not name:
            continue
        cat.register(
            ToolNameEntry(
                name=name,
                kind=str(h.get("kind") or "skill"),
                description_short=str(h.get("description") or ""),
                source=str(h.get("source") or "discovery"),
                estimated_schema_tokens=estimate_schema_tokens(str(h.get("description") or "")),
            )
        )
    return cat
