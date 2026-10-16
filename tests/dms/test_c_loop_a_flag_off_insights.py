"""Flag-off /v1/insights stays byte-identical when the OV refusal check is a no-op.

``freeroute.complete`` calls ``openvault_policy_refusal`` on every call,
including a flag-off insights generate. The check returns "" unless OpenVault
already failed with ``not_in_catalog``. Only that case rewrites the reason
and the stamp. A successful call must match the bytes main would return,
which has no such rewrite.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from CortexOS.integrations import freeroute
from tests.dms.test_insights_served_passthrough import (  # noqa: F401
    BEARER,
    _queue_think_then_sql,
    api,  # noqa: F401
    local_vault,  # noqa: F401
)

_INTENT = {"intent": "how many skus", "ask": False, "generate": True}


def _install_clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    clock = {"n": 0}

    def _uuid4() -> uuid.UUID:
        clock["n"] += 1
        return uuid.UUID(int=clock["n"])

    monkeypatch.setattr(freeroute.uuid, "uuid4", _uuid4)
    monkeypatch.setattr(freeroute.time, "time", lambda: 1_700_000_000.0)
    return clock


def _post_insights(
    client,
    fake,
    monkeypatch: pytest.MonkeyPatch,
    policy,
    scoreboard: Path,
    clock: dict[str, int],
) -> bytes:
    clock["n"] = 0
    if scoreboard.exists():
        scoreboard.unlink()
    monkeypatch.setenv(freeroute.STORE_ENV, str(scoreboard))
    monkeypatch.delenv("CORTEX_PLAN_SQL", raising=False)
    freeroute.reset()
    fake.replies.clear()
    _queue_think_then_sql(fake)
    monkeypatch.setattr(freeroute, "openvault_policy_refusal", policy)
    res = client.post(
        "/v1/insights",
        json=_INTENT,
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("model_called") is True
    return res.content


def test_policy_refusal_rewrites_only_not_in_catalog() -> None:
    """A usable 200 is empty. not_in_catalog is the only rewrite."""
    usable = {
        "choices": [{"message": {"content": "SELECT 1"}}],
        "model": "served-model",
    }
    assert freeroute.openvault_policy_refusal(usable) == ""
    other = {"error": {"type": "rate_limit", "message": "HTTP 429"}}
    assert freeroute.openvault_policy_refusal(other) == ""
    refused = {
        "error": {
            "type": "pin_unavailable",
            "reason": "not_in_catalog",
            "model": "missing-model",
        }
    }
    reason = freeroute.openvault_policy_refusal(refused)
    assert reason.startswith(freeroute.MODEL_NOT_ALLOWLISTED)
    assert "not_in_catalog" in reason
    assert "missing-model" in reason


def test_flag_off_insights_success_is_byte_identical_to_main(
    api, local_vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path  # noqa: F811
) -> None:
    """Successful flag-off POST /v1/insights matches main's response bytes.

    The new check runs (the counter moves) and returns "" on this 200, so
    reason text and the stamp stay as they are. Main has no rewrite; forcing
    the check to return "" must produce the same body.
    """
    monkeypatch.delenv("CORTEX_PLAN_SQL", raising=False)
    clock = _install_clock(monkeypatch)
    scoreboard = tmp_path / "scoreboard.db"
    real = freeroute.openvault_policy_refusal
    live_reasons: list[str] = []
    main_calls = {"n": 0}

    def _live(data: object) -> str:
        reason = real(data)
        live_reasons.append(reason)
        return reason

    def _main_no_rewrite(_data: object) -> str:
        main_calls["n"] += 1
        return ""

    live = _post_insights(api, local_vault, monkeypatch, _live, scoreboard, clock)
    main_bytes = _post_insights(api, local_vault, monkeypatch, _main_no_rewrite, scoreboard, clock)
    assert live_reasons, "openvault_policy_refusal did not run on the flag-off insights call"
    assert all(reason == "" for reason in live_reasons)
    assert main_calls["n"] == len(live_reasons)
    assert live == main_bytes
