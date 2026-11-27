"""CORTEX-FREEROUTE-ROLE: OpenVault ``role`` on the chat body, default off.

Collected by the existing ``python -m pytest tests/`` CI step. No new workflow.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from CortexOS.integrations import freeroute as fr

MESSAGES = [{"role": "user", "content": "role-01"}]

# One production task per OpenVault role. Aliases are asserted with these.
_ROLE_TASKS = (
    ("plan-sql-sql", "sql"),
    ("plan-sql-sql-strong", "sql"),
    ("gen-ask-sql", "sql"),
    ("crew-insights-sql", "sql"),
    ("crew-think", "reason"),
    ("plan-sql-reconfirm", "reason"),
    ("lane-route", "reason"),
    ("dms-metric-check", "reason"),
    ("crew-insights", "summarize"),
    ("doc-answer", "summarize"),
    ("crew-act", "tool"),
    ("grain", "grain"),
    ("ontology", "ontology"),
    ("dms-metric-choose", "ontology"),
)


def _bytes(body: dict[str, Any]) -> bytes:
    """Bytes ``request_json`` would send: json.dumps of the dict, default separators."""
    return json.dumps(dict(body)).encode("utf-8")


def _today(model: str, *, strict: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": MESSAGES,
        "max_tokens": 600,
        "stream": False,
    }
    if strict:
        body["strict"] = True
    return body


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(fr.ROLE_ENV, raising=False)
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)


def test_role_flag_off_body_bytes_match_today(armed_openvault, monkeypatch) -> None:
    """Flag off: a task that would map still posts today's bytes, with no role."""
    _off(monkeypatch)
    out = fr.complete("plan-sql-sql", list(MESSAGES))
    assert out.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert "role" not in sent
    assert "strict" not in sent
    assert _bytes(sent) == _bytes(_today(sent["model"]))


def test_role_flag_off_strict_bytes_match_today(armed_openvault, monkeypatch) -> None:
    """Role flag off does not change the strict body."""
    _off(monkeypatch)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    out = fr.complete("plan-sql-sql", list(MESSAGES))
    assert out.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert "role" not in sent
    assert sent["strict"] is True
    assert _bytes(sent) == _bytes(_today(sent["model"], strict=True))


@pytest.mark.parametrize(("task", "role"), _ROLE_TASKS)
def test_role_flag_on_sends_mapped_role_and_omits_strict(
    armed_openvault, monkeypatch, task: str, role: str
) -> None:
    """Flag on: the body has the mapped role and no strict, even if strict is on."""
    _off(monkeypatch)
    monkeypatch.setenv(fr.ROLE_ENV, "1")
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    out = fr.complete(task, list(MESSAGES))
    assert out.ok is True
    sent = dict(armed_openvault.chat_calls[-1]["body"])
    assert sent.pop("role") == role
    assert "strict" not in sent
    assert _bytes(sent) == _bytes(_today(sent["model"]))


def test_explicit_role_overrides_the_task(armed_openvault, monkeypatch) -> None:
    """A caller-supplied role is the one OpenVault sees."""
    _off(monkeypatch)
    monkeypatch.setenv(fr.ROLE_ENV, "1")
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    out = fr.complete("plan-sql-sql", list(MESSAGES), role="summarize")
    assert out.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert sent["role"] == "summarize"
    assert "strict" not in sent


def test_unmapped_task_omits_role_and_keeps_strict(armed_openvault, monkeypatch) -> None:
    """A task with no role still sends strict. The two fields are never paired."""
    _off(monkeypatch)
    monkeypatch.setenv(fr.ROLE_ENV, "1")
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    out = fr.complete("t", list(MESSAGES))
    assert out.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert "role" not in sent
    assert sent["strict"] is True
    assert _bytes(sent) == _bytes(_today(sent["model"], strict=True))


def test_tools_on_an_unnamed_task_map_to_tool(armed_openvault, monkeypatch) -> None:
    _off(monkeypatch)
    monkeypatch.setenv(fr.ROLE_ENV, "1")
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    tool = {"type": "function", "function": {"name": "lookup"}}
    out = fr.complete("t", list(MESSAGES), tools=[tool])
    assert out.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert sent["role"] == "tool"
    assert sent["tools"] == [tool]
    assert "strict" not in sent
