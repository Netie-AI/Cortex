"""Constructor agent roster + inbox."""
from __future__ import annotations

import pytest

from CortexOS.connectors import agents, cursor_session


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    agents.reset_for_tests()
    yield
    agents.reset_for_tests()
    cursor_session.reset_for_tests()


def test_roster_includes_constructor_and_pointer():
    ids = {a["id"] for a in agents.roster()}
    assert "constructor" in ids
    assert "pointer" in ids
    pointer = next(a for a in agents.roster() if a["id"] == "pointer")
    assert "computer_control" in pointer
    assert pointer["computer_control"]["armed"] is False


def test_post_constructor_opens_cursor_on_cortex():
    out = agents.post("constructor", "build the operator desk")
    assert out["dispatch"]["workspace"] == "cortex"
    assert out["dispatch"]["new_cursor_chat"] is True
    texts = [m["text"] for m in agents.messages("constructor")]
    assert "build the operator desk" in texts
    assert any("Constructor Agent took it" in t for t in texts)


def test_unknown_agent_raises():
    with pytest.raises(KeyError):
        agents.get("money-gainer")
