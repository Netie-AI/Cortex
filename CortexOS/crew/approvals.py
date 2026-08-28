"""Per-agent tool-approval layer stacked on top of ``CortexOS.crew.policy``.

The DeepAgents human-in-the-loop pattern (approve / edit / reject a tool call
before it runs) absorbed as data a ``spawn_agent`` call can carry, so one
teammate can be watched more closely than the crew default without the
operator re-arming anything.

The security property this module exists to hold: **an agent's approval policy
may only make a decision stricter, never looser.** ``policy.decide`` already
owns the master switch (``CORTEX_COMPUTER_CONTROL``), per-server arming and the
per-call confirm on mutating tools. If a per-agent ``auto_tools`` list could
turn that layer's CONFIRM or DENY into an ALLOW, then any model that can call
``spawn_agent`` could mint itself an unattended computer-control agent - the
gate would be reachable from inside the thing it gates. So ``auto_tools`` is
read *only* where the base decision was already ALLOW; every other arm can add
friction and none can remove it.

Refusals carry their reason and name the layer that produced it, because a
transcript that shows "DENIED" without saying which gate fired reads as a bug
rather than a decision (KB R-0011).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from CortexOS.crew.policy import ALLOW, CONFIRM, DENY, TAKEOVER

# How much friction each decision carries. The final decision must never rank
# below the base one; ``_strictest`` is the only place that comparison lives.
_RANK: dict[str, int] = {ALLOW: 0, CONFIRM: 1, TAKEOVER: 2, DENY: 3}

LAYER_POLICY = "crew policy"
LAYER_AGENT = "agent approvals"


def _names(tool: str, server: str | None) -> set[str]:
    """Every spelling of one tool call, matching ``policy.decide``'s matching.

    The operator writes ``Type`` or ``windows-mcp.Type`` in a grant; the model
    sees the sanitized ``mcp_windows-mcp_Type``. All three must hit the same
    rule or a reject list would be silently bypassed by whichever spelling the
    caller happened to use.
    """
    names = {tool}
    if server:
        names.add(f"{server}.{tool}")
        names.add(f"mcp_{server}_{tool}")
    return names


def _tool_list(val: Any) -> tuple[str, ...]:
    """Parse a tool-name list off spawn args: list, JSON array, or CSV string.

    Tool lists arrive from an LLM tool call, so they are as likely to be the
    string ``"Type, Click"`` as a real list. Dropping an unparsed list silently
    would turn a reject list into no protection at all.
    """
    if isinstance(val, str):
        text = val.strip()
        if not text:
            return ()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return tuple(str(x).strip() for x in parsed if str(x).strip())
        return tuple(p.strip() for p in text.split(",") if p.strip())
    if isinstance(val, Iterable):
        return tuple(str(x).strip() for x in val if str(x).strip())
    return ()


@dataclass(frozen=True)
class ApprovalPolicy:
    """One agent's human-in-the-loop arms. Every field can only add friction.

    ``approve_tools`` - always take the per-call operator confirm, even where
    ``policy.decide`` said ALLOW (a read-only tool the operator still wants to
    watch land).
    ``auto_tools`` - skip the confirm, but *only* where the base decision was
    already ALLOW. Never an upgrade path out of CONFIRM, TAKEOVER or DENY.
    ``reject_tools`` - always DENY, with ``reject_reason`` in the refusal.
    """

    approve_tools: frozenset[str] = field(default_factory=frozenset)
    auto_tools: frozenset[str] = field(default_factory=frozenset)
    reject_tools: frozenset[str] = field(default_factory=frozenset)
    reject_reason: str = "on the agent's reject list"

    @classmethod
    def from_spawn_args(cls, args: Mapping[str, Any] | None) -> ApprovalPolicy:
        """Build from a ``spawn_agent`` tool call's raw arguments."""
        args = args or {}
        return cls(
            approve_tools=frozenset(_tool_list(args.get("approve_tools"))),
            auto_tools=frozenset(_tool_list(args.get("auto_tools"))),
            reject_tools=frozenset(_tool_list(args.get("reject_tools"))),
            reject_reason=str(args.get("reject_reason") or "on the agent's reject list"),
        )

    def is_empty(self) -> bool:
        """True when this policy has nothing to say - the base decision stands."""
        return not (self.approve_tools or self.auto_tools or self.reject_tools)

    def as_dict(self) -> dict[str, list[str]]:
        """Sorted plain data, for persisting on an agent row or a transcript."""
        return {
            "approve_tools": sorted(self.approve_tools),
            "auto_tools": sorted(self.auto_tools),
            "reject_tools": sorted(self.reject_tools),
        }


EMPTY = ApprovalPolicy()


def _strictest(base: str, candidate: str) -> str:
    """Whichever of the two carries more friction. Unknown names read as DENY."""
    return candidate if _RANK.get(candidate, 3) > _RANK.get(base, 3) else base


def decide_with_approvals(
    base: tuple[str, str],
    tool: str,
    *,
    server: str | None = None,
    approvals: ApprovalPolicy | None = None,
) -> tuple[str, str]:
    """Fold an agent's :class:`ApprovalPolicy` into ``policy.decide``'s verdict.

    ``base`` is the ``(decision, reason)`` pair straight from ``policy.decide``.
    The result is never less strict than ``base``: ``auto_tools`` is consulted
    only on the ALLOW arm, so master-switch, arming and mutating-tool confirms
    survive any per-agent configuration. The returned reason names the layer
    that decided so the transcript can show why nothing happened.
    """
    decision, reason = base
    pol = approvals or EMPTY

    if decision not in _RANK:
        # An unrecognised verdict is not something to interpret optimistically.
        return DENY, f"{LAYER_POLICY}: unknown decision {decision!r} for '{tool}'"

    names = _names(tool, server)

    if names & pol.reject_tools:
        return DENY, f"{LAYER_AGENT}: '{tool}' is {pol.reject_reason}"

    if decision == DENY:
        return DENY, f"{LAYER_POLICY}: {reason}"

    if decision in (CONFIRM, TAKEOVER):
        if names & pol.auto_tools:
            # Named and refused on purpose: silence here would let an operator
            # believe a tool was auto-approved when every call still stops.
            return decision, (
                f"{LAYER_POLICY}: {reason} "
                f"(agent auto_tools cannot loosen a {decision} decision)"
            )
        return decision, f"{LAYER_POLICY}: {reason}"

    # decision == ALLOW: the only arm where the agent layer may act.
    if names & pol.approve_tools:
        return _strictest(decision, CONFIRM), (
            f"{LAYER_AGENT}: '{tool}' is on the agent's approve list, "
            "operator confirm required"
        )
    if names & pol.auto_tools:
        return ALLOW, f"{LAYER_AGENT}: '{tool}' auto-approved within a policy ALLOW"
    return ALLOW, f"{LAYER_POLICY}: {reason}"


@dataclass(frozen=True)
class PendingCall:
    """A tool call parked for the operator's approve / edit / reject.

    Deliberately carries no decision. The "edit" arm of the pattern returns a
    *new* PendingCall, so an amended call cannot inherit the approval given to
    the call it replaced - the caller has to re-run the gate on what will
    actually execute. Amending the tool name would otherwise be a way to walk
    an approval for a read-only screenshot onto a mutating click.
    """

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    server: str | None = None
    revision: int = 0
    revised_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Freeze the args: a mutable dict held by both the transcript and the
        # executor is an edit nobody approved.
        object.__setattr__(self, "args", MappingProxyType(dict(self.args)))

    def revise(
        self,
        *,
        tool: str | None = None,
        args: Mapping[str, Any] | None = None,
        by: str = "operator",
    ) -> PendingCall:
        """Return the amended call. The original is left untouched, undecided."""
        return PendingCall(
            tool=tool if tool is not None else self.tool,
            args=dict(args) if args is not None else dict(self.args),
            server=self.server,
            revision=self.revision + 1,
            revised_by=(*self.revised_by, by),
        )

    def decide(
        self,
        base: tuple[str, str],
        *,
        approvals: ApprovalPolicy | None = None,
    ) -> tuple[str, str]:
        """Gate *this* call - the revised tool name, not the one it replaced."""
        return decide_with_approvals(
            base, self.tool, server=self.server, approvals=approvals
        )

    def as_dict(self) -> dict[str, Any]:
        """Plain data for the transcript: what was asked, and how it was edited."""
        return {
            "tool": self.tool,
            "args": dict(self.args),
            "server": self.server,
            "revision": self.revision,
            "revised_by": list(self.revised_by),
        }
