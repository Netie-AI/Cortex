from __future__ import annotations

import itertools

import pytest

from CortexOS.crew import policy
from CortexOS.crew.approvals import (
    EMPTY,
    LAYER_AGENT,
    LAYER_POLICY,
    ApprovalPolicy,
    PendingCall,
    decide_with_approvals,
)

_RANK = {policy.ALLOW: 0, policy.CONFIRM: 1, policy.TAKEOVER: 2, policy.DENY: 3}


def test_empty_policy_passes_the_base_decision_through() -> None:
    base = policy.decide("Screenshot", server="windows-mcp", armed=True, master_on=True)
    decision, reason = decide_with_approvals(base, "Screenshot", server="windows-mcp")
    assert decision == policy.ALLOW
    assert reason.startswith(LAYER_POLICY)
    assert "read-only capture tool" in reason
    assert EMPTY.is_empty() is True


def test_auto_tools_cannot_upgrade_a_confirm() -> None:
    # The rule the whole module exists for: a mutating tool on an armed server
    # still stops for the operator, no matter what the agent was spawned with.
    base = policy.decide("Type", server="windows-mcp", armed=True, master_on=True)
    assert base[0] == policy.CONFIRM

    loose = ApprovalPolicy(auto_tools=frozenset({"Type", "windows-mcp.Type"}))
    decision, reason = decide_with_approvals(
        base, "Type", server="windows-mcp", approvals=loose
    )
    assert decision == policy.CONFIRM
    assert "cannot loosen" in reason
    assert reason.startswith(LAYER_POLICY)


def test_auto_tools_cannot_upgrade_a_deny() -> None:
    for armed, master_on in ((True, False), (False, True), (False, False)):
        base = policy.decide(
            "Screenshot", server="windows-mcp", armed=armed, master_on=master_on
        )
        assert base[0] == policy.DENY
        loose = ApprovalPolicy(auto_tools=frozenset({"Screenshot"}))
        decision, reason = decide_with_approvals(
            base, "Screenshot", server="windows-mcp", approvals=loose
        )
        assert decision == policy.DENY
        # the refusal still names the fix, not just "denied"
        assert ("CORTEX_COMPUTER_CONTROL" in reason) or ("not armed" in reason)
        assert reason.startswith(LAYER_POLICY)


def test_auto_tools_cannot_upgrade_a_takeover() -> None:
    decision, reason = decide_with_approvals(
        (policy.TAKEOVER, "login wall"),
        "Type",
        server="windows-mcp",
        approvals=ApprovalPolicy(auto_tools=frozenset({"Type"})),
    )
    assert decision == policy.TAKEOVER
    assert "cannot loosen" in reason


def test_auto_tools_only_bites_inside_an_allow() -> None:
    base = policy.decide("Screenshot", server="windows-mcp", armed=True, master_on=True)
    decision, reason = decide_with_approvals(
        base,
        "Screenshot",
        server="windows-mcp",
        approvals=ApprovalPolicy(auto_tools=frozenset({"Screenshot"})),
    )
    assert decision == policy.ALLOW
    assert reason.startswith(LAYER_AGENT)
    assert "within a policy ALLOW" in reason


def test_approve_tools_makes_an_allow_stop_for_the_operator() -> None:
    base = policy.decide("Screenshot", server="windows-mcp", armed=True, master_on=True)
    assert base[0] == policy.ALLOW
    decision, reason = decide_with_approvals(
        base,
        "Screenshot",
        server="windows-mcp",
        approvals=ApprovalPolicy(approve_tools=frozenset({"Screenshot"})),
    )
    assert decision == policy.CONFIRM
    assert reason.startswith(LAYER_AGENT)
    assert "approve list" in reason


def test_approve_beats_auto_when_a_tool_is_on_both_lists() -> None:
    base = (policy.ALLOW, "read-only capture tool on an armed server")
    decision, _ = decide_with_approvals(
        base,
        "Screenshot",
        server="windows-mcp",
        approvals=ApprovalPolicy(
            approve_tools=frozenset({"Screenshot"}),
            auto_tools=frozenset({"Screenshot"}),
        ),
    )
    assert decision == policy.CONFIRM


def test_reject_tools_deny_every_base_decision_with_a_reason() -> None:
    pol = ApprovalPolicy(
        reject_tools=frozenset({"Screenshot"}),
        reject_reason="off-limits for this teammate",
    )
    for base_decision in (policy.ALLOW, policy.CONFIRM, policy.TAKEOVER, policy.DENY):
        decision, reason = decide_with_approvals(
            (base_decision, "base"), "Screenshot", server="windows-mcp", approvals=pol
        )
        assert decision == policy.DENY
        assert reason.startswith(LAYER_AGENT)
        assert "off-limits for this teammate" in reason


def test_reject_beats_approve_and_auto() -> None:
    decision, _ = decide_with_approvals(
        (policy.ALLOW, "base"),
        "Screenshot",
        server="windows-mcp",
        approvals=ApprovalPolicy(
            approve_tools=frozenset({"Screenshot"}),
            auto_tools=frozenset({"Screenshot"}),
            reject_tools=frozenset({"Screenshot"}),
        ),
    )
    assert decision == policy.DENY


@pytest.mark.parametrize(
    "spelling",
    ["Type", "windows-mcp.Type", "mcp_windows-mcp_Type"],
)
def test_every_spelling_of_a_tool_hits_the_reject_list(spelling: str) -> None:
    # The operator writes one form, the model sees another. A reject list that
    # matched only one spelling would be no protection at all.
    decision, _ = decide_with_approvals(
        (policy.CONFIRM, "base"),
        "Type",
        server="windows-mcp",
        approvals=ApprovalPolicy(reject_tools=frozenset({spelling})),
    )
    assert decision == policy.DENY


def test_internal_tools_can_be_rejected_and_watched_per_agent() -> None:
    base = policy.decide("write_file", server=None, armed=False, master_on=False)
    assert base[0] == policy.ALLOW

    watched = ApprovalPolicy(approve_tools=frozenset({"write_file"}))
    assert decide_with_approvals(base, "write_file", approvals=watched)[0] == policy.CONFIRM

    banned = ApprovalPolicy(reject_tools=frozenset({"write_file"}))
    assert decide_with_approvals(base, "write_file", approvals=banned)[0] == policy.DENY


def test_layer_is_never_looser_than_policy_across_the_matrix() -> None:
    """The security property, asserted exhaustively rather than by example."""
    tools = ["Screenshot", "Type", "BrandNewTool", "uacc_query"]
    greedy = ApprovalPolicy(
        auto_tools=frozenset(
            {n for t in tools for n in (t, f"windows-mcp.{t}", f"mcp_windows-mcp_{t}")}
        )
    )
    for tool, armed, master_on in itertools.product(tools, (True, False), (True, False)):
        base = policy.decide("" + tool, server="windows-mcp", armed=armed, master_on=master_on)
        final = decide_with_approvals(base, tool, server="windows-mcp", approvals=greedy)
        assert _RANK[final[0]] >= _RANK[base[0]], (tool, armed, master_on, base, final)


def test_unknown_base_decision_fails_closed() -> None:
    decision, reason = decide_with_approvals(("maybe", "?"), "Screenshot")
    assert decision == policy.DENY
    assert "unknown decision" in reason


def test_from_spawn_args_parses_list_json_and_csv() -> None:
    pol = ApprovalPolicy.from_spawn_args(
        {
            "approve_tools": ["Screenshot"],
            "auto_tools": '["uacc_query", "Wait"]',
            "reject_tools": "Type, Click ,",
        }
    )
    assert pol.approve_tools == frozenset({"Screenshot"})
    assert pol.auto_tools == frozenset({"uacc_query", "Wait"})
    assert pol.reject_tools == frozenset({"Type", "Click"})
    assert pol.as_dict()["reject_tools"] == ["Click", "Type"]

    empty = ApprovalPolicy.from_spawn_args(None)
    assert empty.is_empty() is True
    assert ApprovalPolicy.from_spawn_args({"auto_tools": 17}).is_empty() is True


def test_revised_call_does_not_inherit_the_original_approval() -> None:
    pending = PendingCall(tool="Screenshot", args={"window": "chrome"}, server="windows-mcp")
    base_allow = policy.decide("Screenshot", server="windows-mcp", armed=True, master_on=True)
    assert pending.decide(base_allow)[0] == policy.ALLOW

    edited = pending.revise(tool="Type", args={"text": "rm -rf"}, by="operator")
    assert edited is not pending
    assert edited.revision == 1
    assert edited.revised_by == ("operator",)
    assert edited.tool == "Type"
    # the original is untouched, and the amended call is re-gated on its own name
    assert pending.tool == "Screenshot"
    assert dict(pending.args) == {"window": "chrome"}

    base_for_edit = policy.decide("Type", server="windows-mcp", armed=True, master_on=True)
    decision, _ = edited.decide(base_for_edit)
    assert decision == policy.CONFIRM

    banned = ApprovalPolicy(reject_tools=frozenset({"Type"}))
    assert edited.decide(base_for_edit, approvals=banned)[0] == policy.DENY


def test_pending_call_args_are_frozen_and_serialisable() -> None:
    args = {"text": "hello"}
    pending = PendingCall(tool="Type", args=args, server="windows-mcp")
    args["text"] = "tampered"
    assert pending.args["text"] == "hello"
    with pytest.raises(TypeError):
        pending.args["text"] = "tampered"  # type: ignore[index]

    amended = pending.revise(args={"text": "goodbye"}, by="operator")
    assert amended.as_dict() == {
        "tool": "Type",
        "args": {"text": "goodbye"},
        "server": "windows-mcp",
        "revision": 1,
        "revised_by": ["operator"],
    }
