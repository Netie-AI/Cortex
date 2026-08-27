from CortexOS.crew import fleet


def test_spine_imports_prd_epic_ticket_and_grok():
    slugs = [a["slug"] for a in fleet.spine()]
    assert slugs == [
        "ticket-runner",
        "prd-agent",
        "epic-agent",
        "pr-bot",
        "verify",
        "gating",
        "decision",
        "grok",
    ]
    names = [a["name"] for a in fleet.starter()]
    assert "PRD Agent" in names
    assert "Ticket Runner" in names
    assert "Epic Agent" in names
    contract = fleet.public_contract()
    assert all("system_prompt" not in row for row in contract)
    ticket = next(a for a in fleet.spine() if a["slug"] == "ticket-runner")
    assert "one agent per issue" in ticket["never"]
    assert "PRD Agent" in ticket["system_prompt"] or "prd" in ticket["system_prompt"].lower()
