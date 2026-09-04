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

from CortexOS.crew.config import BACKEND_CF_COMPUTER, BACKEND_LAPTOP, FLAG_CF_COMPUTER

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
        "estate_status",
        "ship_gate",
        "rename_agent",
        "stop_agent",
        "kill_agent",
        "set_agent_mode",
        "show_issue",
        "request_close",
        "ws_ls",
        "ws_read",
        "ws_write",
        "ws_edit",
        "ws_glob",
    }
)

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"
TAKEOVER = "takeover"

# Existing approved local exec (github.py / estate gh). Unknown binaries fail closed.
LOCAL_SHELL_ALLOWLIST = frozenset({"gh"})
# gh verbs that change remote state. list/view/status stay allow after argv[0] match.
MUTATING_GH_VERBS = frozenset(
    {
        "create",
        "close",
        "merge",
        "delete",
        "edit",
        "ready",
        "review",
        "comment",
        "release",
        "workflow",
        "api",
        "gist",
        "secret",
        "auth",
        "login",
    }
)
# `gh auth status` is a probe, not a login. Allow that exact shape.
GH_AUTH_STATUS = ("gh", "auth", "status")

# Arg keys / tool names that mean the operator should type, not the agent.
_AUTH_KEYS = frozenset({"password", "passwd", "pass", "secret", "token", "otp", "totp", "pin"})
_AUTH_HINTS = ("password", "passwd", "login", "signin", "sign-in", "otp", "2fa", "totp", "auth")


def needs_takeover(tool: str, args: dict | None = None) -> bool:
    """True when this confirm is a login/2FA wall. UI shows the takeover popup."""
    parts = [tool]
    for key, val in (args or {}).items():
        parts.append(str(key))
        parts.append(str(val)[:80])
    blob = " ".join(parts).lower()
    if any(h in blob for h in _AUTH_HINTS):
        return True
    for key in args or {}:
        if str(key).lower() in _AUTH_KEYS:
            return True
    return False


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


def _argv0(argv: list[str]) -> str:
    if not argv:
        return ""
    name = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def decide_runtime(
    argv: list[str],
    *,
    backend: str,
    isolate_enabled: bool,
    approved: bool = False,
) -> tuple[str, str]:
    """Gate one dual-path exec. Laptop uses the existing gh allowlist; isolate
    additionally requires CREW_CF_COMPUTER=1. Unknown binaries never run."""
    if not argv or not str(argv[0]).strip():
        return DENY, "empty argv"
    backend = (backend or "").strip().lower().replace("_", "-")
    if backend not in {BACKEND_LAPTOP, BACKEND_CF_COMPUTER}:
        return DENY, f"unknown runtime backend '{backend}'"
    if backend == BACKEND_CF_COMPUTER and not isolate_enabled:
        return DENY, (
            f"cloudflare-computer isolate is off: set {FLAG_CF_COMPUTER}=1 "
            "(PREVIEW only, not production)"
        )
    binary = _argv0(argv)
    if binary not in LOCAL_SHELL_ALLOWLIST:
        return DENY, f"local tool '{binary or argv[0]}' is not on the approved allowlist"
    tokens = [str(p).lower() for p in argv]
    mutating = [t for t in tokens[1:] if t in MUTATING_GH_VERBS]
    if tuple(tokens[:3]) == GH_AUTH_STATUS:
        mutating = [t for t in mutating if t != "auth"]
    if mutating:
        if approved:
            return ALLOW, "operator approved mutating local tool"
        return CONFIRM, (
            "mutating local tool needs operator approval: " + ", ".join(dict.fromkeys(mutating))
        )
    return ALLOW, f"approved local tool on {backend}"
