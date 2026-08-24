"""Tool gate for crew agents - the P16 Request tier applied to third-party MCP.

Aligned with ``CortexOS/connectors/computer_control.py`` (the Constructor
desk's probe): the same master switch ``CORTEX_COMPUTER_CONTROL=1`` guards
everything, each MCP server must additionally be armed by the operator in the
UI, and - stricter than the probe - every mutating call still needs a per-call
human Approve. Read-only capture tools skip only the per-call confirm, never
the master switch or arming. A refusal is always returned with its reason so
the agent (and the transcript) states why nothing happened; a control that
refuses silently would read as a hang (KB R-0011).
"""

from __future__ import annotations

# Windows-MCP capture/timing tools that observe but do not act. Everything not
# listed here is treated as mutating - fail closed on unknown tools.
READ_ONLY_TOOLS = frozenset(
    {
        "Screenshot",
        "Snapshot",
        "Scrape",
        "DisplayInventory",
        "Wait",
        "WaitFor",
        "State",
        # UACC observation tools (names as exposed by the MCP server)
        "screenshot",
        "get_screen_info",
        "get_screen_info_enhanced",
        "list_windows",
        "list_monitors",
        "get_active_window",
        "get_mouse_position",
        "list_processes",
        "get_system_info",
        "take_snapshot",
        "compare_snapshots",
        "get_screen_diff",
        "memory_summary",
        "query_knowledge",
        "uacc_query",
        "uacc_where_is",
    }
)

# Crew-internal tools: orchestration and governed engine reads. cortex_ask is
# a read against the running engine's own governed answer path (the engine
# enforces its bindings and abstains where it must - crew adds nothing and
# removes nothing there).
INTERNAL_TOOLS = frozenset(
    {
        "spawn_agent",
        "send_to_agent",
        "ask_agent",
        "broadcast",
        "wait_for_replies",
        "cortex_ask",
        "finish",
        "netie_board",
        "desk_status",
        "rename_agent",
    }
)

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"


def decide(
    tool: str,
    *,
    server: str | None,
    armed: bool,
    master_on: bool,
    allowed: frozenset[str] | None = None,
    denied: frozenset[str] | None = None,
) -> tuple[str, str]:
    """Return (decision, reason) for one tool call.

    ``server`` is None for crew-internal tools and the MCP server name for
    third-party tools. ``allowed`` / ``denied`` are per-agent grants on the
    shared connector pool: empty allowed means share everything that already
    passed master/arm/confirm; a deny always wins.
    """
    names = {tool}
    if server:
        names.add(f"{server}.{tool}")
    if denied and names & denied:
        return DENY, f"agent grant denies '{tool}'"
    if server is None:
        if tool in INTERNAL_TOOLS:
            return ALLOW, "crew-internal tool"
        return DENY, f"unknown internal tool '{tool}'"
    if allowed and not (names & allowed):
        return DENY, f"agent grant does not allow '{tool}'"
    if not master_on:
        return DENY, "computer control is off: set CORTEX_COMPUTER_CONTROL=1 and restart"
    if not armed:
        return DENY, f"MCP server '{server}' is not armed (arm it in the Computer control panel)"
    if tool in READ_ONLY_TOOLS:
        return ALLOW, "read-only capture tool on an armed server"
    return CONFIRM, "mutating computer-control tool needs operator approval"
