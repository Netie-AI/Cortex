"""Cortex owns Idea-to-Paper system prompts (AirGPT is shell + keys)."""

from CortexOS.execution import prompt_library


def test_idea_to_paper_builtin_renders_stage_and_topic():
    body = prompt_library.render(
        "research.idea_to_paper",
        {"topic": "local citation gate", "stage": "execute"},
    )
    assert "Stage: execute" in body
    assert "local citation gate" in body
    assert "OpenVault holds keys" in body
    assert "You are Cortex Idea-to-Paper" in body
