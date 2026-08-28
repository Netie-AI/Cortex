from CortexOS.crew.mcp_client import DEFAULT_SPECS
from CortexOS.crew.roles import by_name, catalog


def test_catalog_names_the_specialists() -> None:
    names = {r["name"] for r in catalog()}
    assert names == {
        "Ticket",
        "PRD",
        "Epic",
        "Gate",
        "Marketing",
        "Money",
        "Decision",
        "PR",
        "Email",
        "Connector",
        "Browser",
        "SEO",
        "Skills",
        "Routines",
        "Watchdog",
        "Security",
        "Reliability",
        "Infra",
        "Architecture",
        "Observability",
        "Surface",
    }
    prd = by_name("prd")
    assert prd is not None and "PRD agent" in prd.role
    marketing = by_name("Marketing")
    assert marketing is not None
    assert marketing.skills == (
        "outreach",
        "chat-human",
        "computer-reach",
        "proposal-artifact",
        "feedback-learn",
    )
    email = by_name("Email")
    assert email is not None
    assert "feedback-learn" in email.skills
    assert "chat-human" in email.skills
    ticket = by_name("Ticket")
    assert ticket is not None
    assert "netie_board" in ticket.role
    assert "cloud agent" in ticket.role
    assert by_name("not-a-role") is None
    assert catalog()[0]["kind"] == "capability"
    from CortexOS.crew.roles import charter_block

    text = charter_block()
    assert "exact name" not in text.lower()
    assert "prompt templates" in text.lower()


def test_default_mcp_catalog_includes_uacc_and_windows() -> None:
    names = [s["name"] for s in DEFAULT_SPECS]
    assert names[:3] == ["uacc", "windows-mcp", "computer-control-mcp"]
    assert all(s["armed"] is False for s in DEFAULT_SPECS)
