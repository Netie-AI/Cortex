"""Context bounds: offload huge tool results, compact old turns to disk.

DeepAgents MIT pattern (evict large tool output; archive older history).
Cortex still owns work-shape via dag_runner + manifest. This module only
keeps the crew model's prompt from growing without a ceiling.
"""

from __future__ import annotations

from typing import Any

from CortexOS.crew.workspace import SpaceWorkspace, preview_large

TOOL_RESULT_LIMIT = 4000


def offload_tool_result(ws: SpaceWorkspace, tool_call_id: str, text: str) -> str:
    """Return text, or a preview plus a workspace pointer when it is too big."""
    body = text if isinstance(text, str) else str(text)
    if len(body) <= TOOL_RESULT_LIMIT:
        return body
    rel = f"large_tool_results/{tool_call_id}.txt"
    ws.write(rel, body)
    shown = preview_large(body)["preview"]
    return (
        f"TOOL RESULT TOO LARGE ({len(body)} chars). Full text: {rel}\n"
        f"Call read_file on that path if you need more.\n\n{shown}"
    )


def archive_turns(
    ws: SpaceWorkspace,
    dropped: list[dict[str, Any]],
    *,
    through_seq: int,
) -> str:
    lines = [f"# compacted through seq {through_seq}", ""]
    for row in dropped:
        role = row.get("role") or "?"
        seq = row.get("seq") or "?"
        body = str(row.get("content") or "").replace("\r\n", "\n")
        lines.append(f"## {seq} {role}")
        lines.append(body[:4000])
        lines.append("")
    rel = f"archives/compact-{through_seq}.md"
    ws.write(rel, "\n".join(lines))
    ws.write("archives/LATEST.txt", rel)
    return rel
