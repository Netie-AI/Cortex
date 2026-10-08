"""FREEROUTE-LEARN-SOFT-01: a learning-row miss must not drop a served answer.

Must-fails (red on main 886e119f, green on this head):

- ``_write_row`` / ``note_verdict`` failure still returns the answer, stamps
  ``learn_row_failed``, logs a WARNING, and increments ``learn_row_failures``.
- A pre-call scoreboard read failure still calls the model inside
  ``DEFAULT_FALLBACK_CAPS``.
- Every non-DMS caller of ``complete`` / ``complete_core`` below is served.

Spend, budget, grant, and ledger failures stay hard.

Non-DMS callers:

- ``CortexOS/crew/freeroute.py:400`` ``complete_core`` -> ``core.complete``
- ``CortexOS/crew/freeroute.py:459`` crew ``complete`` -> ``complete_core``
- ``CortexOS/crew/openvault.py:144`` ``chat`` -> ``complete_core``
- ``CortexOS/crew/server.py:1079`` unarmed ``POST /crew/freeroute`` (no model
  answer; stays 409; no learning write)
- ``CortexOS/crew/server.py:1086`` armed ``POST /crew/freeroute``
- ``CortexOS/crew/insights.py:1494`` ``fn = complete or fr.complete``
- ``CortexOS/crew/cot_climb.py:647`` ``runner = complete or fr.complete``
  (invoked from ``_call_runner`` at :655, :715, :722, :828)
- ``CortexOS/crew/prompt_harness_climb.py:306`` ``runner = inner or fr.complete``
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import openvault
from CortexOS.crew.config import CrewSettings
from CortexOS.crew.cot_climb import _call_runner
from CortexOS.crew.freeroute import complete as crew_complete
from CortexOS.crew.freeroute import complete_core
from CortexOS.crew.prompt_harness_climb import _wrap_complete
from CortexOS.crew.server import create_app
from CortexOS.integrations import freeroute as fr
from tests.test_crew.conftest import FakeLLM

SERVED = "SERVED-LEARN-SOFT"
PROMPT = "PROMPT_SECRET_learn_soft"
_LOG = "CortexOS.integrations.freeroute"


def _only_models(fake, models: list[str]) -> None:
    fake.hops = [fake.hop("groq", 10)]
    fake.catalogue = {"groq": list(models)}


@contextmanager
def _exclusive(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """Hold BEGIN EXCLUSIVE. Connect timeout is short so the miss is fast."""
    fr.reset()
    path = fr.store_path()
    fr._init_file(path)
    holder = sqlite3.connect(str(path), timeout=1.0)
    holder.execute("BEGIN EXCLUSIVE")
    real = sqlite3.connect

    def _fast(target, *args, **kwargs):
        kwargs["timeout"] = 0.05
        return real(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _fast)
    try:
        yield holder
    finally:
        holder.rollback()
        holder.close()


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING and rec.name == _LOG
    ]


def _assert_served_stamp(stamp: fr.RouteStamp, *, failed: bool) -> None:
    assert stamp.learn_row_failed is failed
    assert stamp.public()["learn_row_failed"] is failed


def test_write_and_verdict_failure_serves_and_counts(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Locked store: answer comes back, both learning writes warn and count."""
    armed_openvault.reply(SERVED)
    with caplog.at_level(logging.WARNING, logger=_LOG):
        with _exclusive(monkeypatch):
            out = fr.complete("t", [{"role": "user", "content": PROMPT}])
            assert out.ok is True
            assert out.text == SERVED
            assert out.stamp is not None
            _assert_served_stamp(out.stamp, failed=True)
            fr.note_verdict(out.stamp, "gate_pass")
            _assert_served_stamp(out.stamp, failed=True)
            assert fr.learn_row_failures() == 2
        notes = _warnings(caplog)
    assert any("learn_row_failed where=_write_row" in line for line in notes)
    assert any("learn_row_failed where=note_verdict" in line for line in notes)
    joined = "\n".join(notes)
    assert PROMPT not in joined
    assert SERVED not in joined
    again = fr.complete("t", [{"role": "user", "content": "second"}])
    assert again.ok is True
    assert again.stamp is not None
    assert again.stamp.learn_row_failed is False
    assert fr.learn_row_failures() == 2


def test_precall_read_failure_uses_default_fallback_caps(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scoreboard read error still sends the call at the default caps.

    Seeded rows would pick model-b. The read raises OSError, so pick explores
    model-a and max_tokens stays the default fallback cap.
    """
    caps = {
        "max_tokens": 600,
        "max_candidates": fr.MAX_CANDIDATES,
        "per_provider": fr.PER_PROVIDER,
        "explore_requests": fr.EXPLORE_REQUESTS,
    }
    live = getattr(fr, "DEFAULT_FALLBACK_CAPS", None)
    if live is not None:
        assert live == caps
        caps = live
    _only_models(armed_openvault, ["model-a", "model-b"])
    monkeypatch.setenv("CORTEX_FREEROUTE_MODELS", "model-a,model-b")
    fr.reset()
    for pin, verdict in (
        ("model-a", "gate_fail"),
        ("model-a", "gate_fail"),
        ("model-b", "gate_pass"),
        ("model-b", "gate_pass"),
    ):
        seeded = fr.complete(
            "learn-soft",
            [{"role": "user", "content": "seed"}],
            pin=pin,
            accept=lambda text: True,
        )
        assert seeded.ok is True
        fr.note_verdict(seeded.stamp, verdict)
    measured = fr.pick("learn-soft", fr.arming(fresh=True))
    assert measured.requested == "model-b"
    assert "measured best" in measured.reason

    real = sqlite3.connect

    class _ReadOSError:
        def __init__(self, con: sqlite3.Connection) -> None:
            self._con = con

        def execute(self, *args, **kwargs):
            sql = args[0] if args else ""
            if isinstance(sql, str) and sql.lstrip().upper().startswith("SELECT"):
                raise OSError("scoreboard unreadable")
            return self._con.execute(*args, **kwargs)

        def close(self) -> None:
            self._con.close()

    def _connect(target, *args, **kwargs):
        con = real(target, *args, **kwargs)
        if isinstance(target, str) and "mode=ro" in target:
            return _ReadOSError(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", _connect)
    armed_openvault.reply(SERVED)
    out = fr.complete("learn-soft", [{"role": "user", "content": PROMPT}])
    assert out.ok is True
    assert out.text == SERVED
    assert out.stamp is not None
    assert out.stamp.requested == "model-a"
    assert out.stamp.pick_reason.startswith("exploring")
    assert len(out.stamp.candidates) <= caps["max_candidates"]
    body = armed_openvault.chat_calls[-1]["body"]
    assert body["max_tokens"] == caps["max_tokens"]
    assert out.stamp.learn_row_failed is False


def test_pack_budget_refusal_stays_hard(armed_openvault) -> None:
    armed_openvault.reply("", status=402, message="pack budget")
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.text == ""
    assert "budget" in out.reason
    assert out.stamp is not None
    assert out.stamp.learn_row_failed is False


def test_token_budget_refusal_stays_hard(armed_openvault) -> None:
    armed_openvault.reply("", status=429, message="token budget")
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert "budget" in out.reason
    assert out.stamp is not None
    assert out.stamp.learn_row_failed is False


def test_non_store_error_stays_hard(
    armed_openvault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ledger-style error is not a learning-row miss. It still raises."""

    class _Boom:
        def execute(self, *args, **kwargs):
            raise RuntimeError("ledger write failed")

        def commit(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(fr, "_open_for_write", lambda path: _Boom())
    with pytest.raises(RuntimeError, match="ledger write failed"):
        fr.complete("t", [{"role": "user", "content": "hi"}])


def test_complete_core_serves(armed_openvault, monkeypatch: pytest.MonkeyPatch) -> None:
    """CortexOS/crew/freeroute.py:400"""
    armed_openvault.reply(SERVED)
    with _exclusive(monkeypatch):
        out = asyncio.run(
            complete_core("t", [{"role": "user", "content": PROMPT}])
        )
    assert out.ok is True
    assert out.text == SERVED
    assert out.stamp is not None and out.stamp.learn_row_failed is True


def test_crew_complete_serves(armed_openvault, monkeypatch: pytest.MonkeyPatch) -> None:
    """CortexOS/crew/freeroute.py:459"""
    armed_openvault.reply(SERVED)
    with _exclusive(monkeypatch):
        out = asyncio.run(crew_complete(prompt=PROMPT, purpose="think"))
    assert out["ok"] is True
    assert out["text"] == SERVED
    assert out["stamp"]["learn_row_failed"] is True


def test_openvault_chat_serves(armed_openvault, monkeypatch: pytest.MonkeyPatch) -> None:
    """CortexOS/crew/openvault.py:144"""
    armed_openvault.reply(SERVED)
    with _exclusive(monkeypatch):
        result = asyncio.run(
            openvault.chat([{"role": "user", "content": PROMPT}])
        )
    assert result.text == SERVED


def test_cot_climb_runner_serves(armed_openvault, monkeypatch: pytest.MonkeyPatch) -> None:
    """CortexOS/crew/cot_climb.py:647 via _call_runner."""
    from CortexOS.crew import freeroute as crew_fr

    armed_openvault.reply(SERVED)
    with _exclusive(monkeypatch):
        out = asyncio.run(
            _call_runner(crew_fr.complete, purpose="think", prompt=PROMPT, bearer=None)
        )
    assert out["ok"] is True
    assert out["text"] == SERVED
    assert out["stamp"]["learn_row_failed"] is True


def test_prompt_harness_runner_serves(
    armed_openvault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CortexOS/crew/prompt_harness_climb.py:306"""
    _only_models(armed_openvault, ["openai/gpt-oss-120b"])
    armed_openvault.reply(SERVED, model="openai/gpt-oss-120b")
    gated = _wrap_complete(
        None, [{"model": "openai/gpt-oss-120b", "kind": "freeroute"}]
    )
    with _exclusive(monkeypatch):
        out = asyncio.run(gated(prompt=PROMPT, purpose="think"))
    assert out["ok"] is True
    assert out["text"] == SERVED
    assert out["stamp"]["learn_row_failed"] is True


def test_insights_caller_serves(armed_openvault, monkeypatch: pytest.MonkeyPatch) -> None:
    """CortexOS/crew/insights.py:1494. A raised complete() becomes ABSTAIN."""
    from CortexOS.crew import freeroute as crew_fr

    async def _call() -> dict:
        try:
            fn = crew_fr.complete
            return await fn(prompt=PROMPT, purpose="think")
        except Exception as exc:  # the insights generate boundary
            return {
                "ok": False,
                "status": "ABSTAIN",
                "refuse_reason": type(exc).__name__,
            }

    armed_openvault.reply(SERVED)
    with _exclusive(monkeypatch):
        out = asyncio.run(_call())
    assert out.get("status") != "ABSTAIN"
    assert out["ok"] is True
    assert out["text"] == SERVED
    assert out["stamp"]["learn_row_failed"] is True


def _crew_settings(tmp_path: Path) -> CrewSettings:
    from CortexOS.crew.board import ensure_skill_packs

    data_dir = tmp_path / "crew"
    ensure_skill_packs(data_dir / "skills")
    return CrewSettings(
        port=0,
        engine_url="http://127.0.0.1:9",
        engine_session="demo",
        data_dir=data_dir,
        master_computer_control=False,
        confirm_timeout_s=5,
        llm_timeout_s=5,
    )


def _mute_key_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openvault, "ingest_cursor_from_files", lambda root=None: {"ok": False})
    monkeypatch.setattr(openvault, "push_env_keys", lambda: {"ok": True, "skipped": True})
    monkeypatch.setattr(openvault, "disable_seeded_cortex_primary", lambda: {"ok": True})
    monkeypatch.setattr(openvault, "list_vault_keys", lambda **kw: [])


def test_server_freeroute_post_serves(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CortexOS/crew/server.py:1086 armed POST /crew/freeroute."""
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    _mute_key_api(monkeypatch)
    fr.reset()
    armed_openvault.reply(SERVED)
    settings = _crew_settings(tmp_path)
    app = create_app(settings, llm_chat=FakeLLM())
    with _exclusive(monkeypatch):
        with TestClient(app, client=("127.0.0.1", 5555)) as client:
            res = client.post(
                "/crew/freeroute",
                json={"purpose": "think", "prompt": PROMPT},
            )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["text"] == SERVED
    assert body["stamp"]["learn_row_failed"] is True


def test_server_unarmed_stays_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CortexOS/crew/server.py:1079 has no served answer and does not write."""
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    monkeypatch.setenv("CORTEX_FREEROUTE", "0")
    before = fr.learn_row_failures()
    app = create_app(_crew_settings(tmp_path), llm_chat=FakeLLM())
    with TestClient(app, client=("127.0.0.1", 5555)) as client:
        res = client.post(
            "/crew/freeroute",
            json={"purpose": "think", "prompt": PROMPT},
        )
    assert res.status_code == 409
    assert res.json()["ok"] is False
    assert fr.learn_row_failures() == before
