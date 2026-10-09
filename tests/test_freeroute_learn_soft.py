"""FREEROUTE-LEARN-SOFT-01: a learning-row miss must not drop a served answer.

Must-fails (red on main 886e119f, green on this head):

- ``_write_row`` / ``note_verdict`` failure still returns the answer, stamps
  ``learn_row_failed``, logs a WARNING, and increments ``learn_row_failures``.
- A pre-call scoreboard read failure clamps ``max_tokens`` to
  ``fallback_max_tokens()`` and stamps ``route_source``. The chat body
  never carries that field. No pin sends ``model="auto"`` without ``pick``.
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
import json
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
# Keys complete() may put on POST /v1/chat/completions. route_source is not one.
# local_only is the existing OV#71 field, sent only when local-only is on.
_OV_CHAT_BODY_KEYS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "stream",
        "temperature",
        "tools",
        "tool_choice",
        "local_only",
    }
)


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


def _assert_chat_body(body: dict, *, max_tokens: int) -> None:
    extra = sorted(set(body) - _OV_CHAT_BODY_KEYS)
    assert not extra, extra
    assert "route_source" not in body
    assert body["max_tokens"] == max_tokens


def _fail_scoreboard_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """SELECT on the read-only scoreboard raises OSError. Writes still run."""
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


def _spy_catalogue(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    seen = {"pick": 0, "candidates": 0}
    real_pick = fr.pick
    real_candidates = fr.candidates

    def pick(*args, **kwargs):
        seen["pick"] += 1
        return real_pick(*args, **kwargs)

    def candidates(*args, **kwargs):
        seen["candidates"] += 1
        return real_candidates(*args, **kwargs)

    monkeypatch.setattr(fr, "pick", pick)
    monkeypatch.setattr(fr, "candidates", candidates)
    return seen


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
    """Healthy read keeps the caller cap. A read miss with no pin sends auto.

    The miss clamps max_tokens, stamps default_fallback, and does not call
    pick or candidates. route_source stays off the OpenVault body. Direct,
    complete_core (2048), and crew think (2048) all take that path.
    """
    assert "max_tokens" not in fr.DEFAULT_FALLBACK_CAPS
    cap = fr.fallback_max_tokens()
    _only_models(armed_openvault, ["model-a", "model-b"])
    monkeypatch.setenv(fr.MODELS_ENV, "model-a,model-b")
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

    armed_openvault.reply("CONTROL")
    control = fr.complete(
        "learn-soft",
        [{"role": "user", "content": "control"}],
        max_tokens=4000,
    )
    assert control.ok is True
    assert control.stamp is not None
    assert control.stamp.requested == "model-b"
    assert control.stamp.route_source == ""
    assert control.stamp.public()["route_source"] == ""
    _assert_chat_body(armed_openvault.chat_calls[-1]["body"], max_tokens=4000)

    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    seen = _spy_catalogue(monkeypatch)
    _fail_scoreboard_reads(monkeypatch)
    armed_openvault.reply(SERVED)
    out = fr.complete(
        "learn-soft",
        [{"role": "user", "content": PROMPT}],
        max_tokens=4000,
    )
    assert out.ok is True
    assert out.text == SERVED
    assert out.stamp is not None
    assert out.stamp.requested == "auto"
    assert out.stamp.pick_reason.startswith("scoreboard unreadable")
    assert seen == {"pick": 0, "candidates": 0}
    _assert_chat_body(armed_openvault.chat_calls[-1]["body"], max_tokens=cap)
    assert armed_openvault.chat_calls[-1]["body"]["model"] == "auto"
    assert out.stamp.route_source == "default_fallback"
    assert out.stamp.public()["route_source"] == "default_fallback"
    assert out.stamp.learn_row_failed is False

    armed_openvault.reply(SERVED)
    via_core = asyncio.run(
        complete_core(
            "learn-soft",
            [{"role": "user", "content": PROMPT}],
            max_tokens=2048,
        )
    )
    assert via_core.stamp is not None
    assert via_core.stamp.requested == "auto"
    assert via_core.stamp.route_source == "default_fallback"
    core_body = armed_openvault.chat_calls[-1]["body"]
    _assert_chat_body(core_body, max_tokens=cap)
    assert core_body["model"] == "auto"

    armed_openvault.reply(SERVED)
    via_crew = asyncio.run(crew_complete(prompt=PROMPT, purpose="think"))
    assert via_crew["ok"] is True
    assert via_crew["stamp"]["requested"] == "auto"
    assert via_crew["stamp"]["route_source"] == "default_fallback"
    crew_body = armed_openvault.chat_calls[-1]["body"]
    _assert_chat_body(crew_body, max_tokens=cap)
    assert crew_body["model"] == "auto"
    assert seen == {"pick": 0, "candidates": 0}


def test_failed_read_operator_pin_and_bundle_win(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pin or MODELS_ENV bundle beats auto. Both clamp and stamp operator_pin."""
    cap = fr.fallback_max_tokens()
    _only_models(armed_openvault, ["catalogue-model"])
    fr.reset()
    fr._init_file(fr.store_path())
    seen = _spy_catalogue(monkeypatch)
    _fail_scoreboard_reads(monkeypatch)

    armed_openvault.reply(SERVED)
    pinned = fr.complete(
        "learn-soft",
        [{"role": "user", "content": PROMPT}],
        max_tokens=4000,
        pin="operator-chosen",
        pin_source="desk",
    )
    assert pinned.ok is True
    assert pinned.stamp is not None
    assert pinned.stamp.requested == "operator-chosen"
    assert pinned.stamp.route_source == "operator_pin"
    assert pinned.stamp.public()["route_source"] == "operator_pin"
    pin_body = armed_openvault.chat_calls[-1]["body"]
    _assert_chat_body(pin_body, max_tokens=cap)
    assert pin_body["model"] == "operator-chosen"

    monkeypatch.setenv(fr.MODELS_ENV, "bundle-a,bundle-b")
    armed_openvault.reply(SERVED)
    bundled = fr.complete(
        "learn-soft",
        [{"role": "user", "content": PROMPT}],
        max_tokens=2048,
    )
    assert bundled.stamp is not None
    assert bundled.stamp.requested == "bundle-a"
    assert bundled.stamp.route_source == "operator_pin"
    bundle_body = armed_openvault.chat_calls[-1]["body"]
    _assert_chat_body(bundle_body, max_tokens=cap)
    assert bundle_body["model"] == "bundle-a"
    assert seen == {"pick": 0, "candidates": 0}


def test_empty_readable_store_is_not_default_fallback(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty table that reads is not a failed read: no stamp, no clamp."""
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    fr.reset()
    fr._init_file(fr.store_path())
    armed_openvault.reply(SERVED)
    out = fr.complete(
        "learn-soft-empty",
        [{"role": "user", "content": PROMPT}],
        max_tokens=4000,
    )
    assert out.ok is True
    assert out.stamp is not None
    assert out.stamp.route_source == ""
    assert out.stamp.public()["route_source"] == ""
    assert fr._precall_read_failed.get() is False
    _assert_chat_body(armed_openvault.chat_calls[-1]["body"], max_tokens=4000)
    assert out.stamp.requested != "auto"


def test_fallback_max_tokens_comes_from_env(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clamp reads FALLBACK_MAX_TOKENS_ENV. A healthy read ignores it."""
    assert fr.fallback_max_tokens() == 600
    monkeypatch.setenv(fr.FALLBACK_MAX_TOKENS_ENV, "250")
    assert fr.fallback_max_tokens() == 250
    fr.reset()
    fr._init_file(fr.store_path())
    armed_openvault.reply(SERVED)
    healthy = fr.complete(
        "learn-soft-cap",
        [{"role": "user", "content": "healthy"}],
        max_tokens=4000,
    )
    assert healthy.stamp is not None
    assert healthy.stamp.route_source == ""
    _assert_chat_body(armed_openvault.chat_calls[-1]["body"], max_tokens=4000)

    _fail_scoreboard_reads(monkeypatch)
    armed_openvault.reply(SERVED)
    missed = fr.complete(
        "learn-soft-cap",
        [{"role": "user", "content": PROMPT}],
        max_tokens=4000,
    )
    assert missed.stamp is not None
    assert missed.stamp.route_source == "default_fallback"
    assert missed.stamp.requested == "auto"
    _assert_chat_body(armed_openvault.chat_calls[-1]["body"], max_tokens=250)


def _wire(body: dict) -> str:
    """Canonical bytes for the outbound chat body. Same fields main sends."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _seed_measured_pair(armed_openvault) -> None:
    """model-a fails twice, model-b passes twice. A healthy pick is model-b."""
    _only_models(armed_openvault, ["model-a", "model-b"])
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


def _count_scoreboard_reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    seen = {"n": 0}
    real = fr._rows

    def _rows(task: str):
        seen["n"] += 1
        return real(task)

    monkeypatch.setattr(fr, "_rows", _rows)
    return seen


def _fail_after_first_select(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """The first read-only SELECT succeeds. Every later one raises OSError."""
    real = sqlite3.connect
    seen = {"selects": 0}

    class _Later:
        def __init__(self, con: sqlite3.Connection) -> None:
            self._con = con

        @property
        def row_factory(self):
            return self._con.row_factory

        @row_factory.setter
        def row_factory(self, value) -> None:
            self._con.row_factory = value

        def execute(self, *args, **kwargs):
            sql = args[0] if args else ""
            if isinstance(sql, str) and sql.lstrip().upper().startswith("SELECT"):
                seen["selects"] += 1
                if seen["selects"] > 1:
                    raise OSError("later scoreboard read failed")
            return self._con.execute(*args, **kwargs)

        def close(self) -> None:
            self._con.close()

    def _connect(target, *args, **kwargs):
        con = real(target, *args, **kwargs)
        if isinstance(target, str) and "mode=ro" in target:
            return _Later(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", _connect)
    return seen


def test_healthy_omitted_max_tokens_matches_main_body(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful read that omits max_tokens matches main 886e119f.

    CORTEX_FREEROUTE_FALLBACK_MAX_TOKENS=123 must not change those bytes.
    Main sends max_tokens 600 for this call.
    """
    _only_models(armed_openvault, ["model-a"])
    monkeypatch.setenv(fr.MODELS_ENV, "model-a")
    fr.reset()
    fr._init_file(fr.store_path())
    messages = [{"role": "user", "content": "healthy-no-max"}]
    main_body = {
        "model": "model-a",
        "messages": messages,
        "max_tokens": fr.HEALTHY_MAX_TOKENS,
        "stream": False,
    }
    main_bytes = _wire(main_body)
    assert '"max_tokens":600' in main_bytes

    monkeypatch.delenv(fr.FALLBACK_MAX_TOKENS_ENV, raising=False)
    armed_openvault.reply(SERVED)
    unset = fr.complete("healthy-bytes", messages)
    unset_body = armed_openvault.chat_calls[-1]["body"]
    assert unset.stamp is not None
    assert unset.stamp.route_source == ""
    assert _wire(unset_body) == main_bytes

    monkeypatch.setenv(fr.FALLBACK_MAX_TOKENS_ENV, "123")
    armed_openvault.reply(SERVED)
    pinned = fr.complete("healthy-bytes", messages)
    pinned_body = armed_openvault.chat_calls[-1]["body"]
    assert pinned.stamp is not None
    assert pinned.stamp.route_source == ""
    assert _wire(pinned_body) == main_bytes
    assert pinned_body["max_tokens"] == 600


def test_complete_reads_the_scoreboard_once(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One complete() performs one scoreboard read, healthy or failed."""
    fr.reset()
    fr._init_file(fr.store_path())
    seen = _count_scoreboard_reads(monkeypatch)
    armed_openvault.reply(SERVED)
    healthy = fr.complete("one-read", [{"role": "user", "content": "ok"}], max_tokens=4000)
    assert healthy.ok is True
    assert seen["n"] == 1

    _fail_scoreboard_reads(monkeypatch)
    armed_openvault.reply(SERVED)
    missed = fr.complete("one-read", [{"role": "user", "content": "miss"}], max_tokens=4000)
    assert missed.stamp is not None
    assert missed.stamp.route_source == "default_fallback"
    assert seen["n"] == 2


def test_later_read_failure_keeps_the_healthy_pick(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second read must not exist. Stamp, model, and cap stay on the first read."""
    monkeypatch.setenv(fr.MODELS_ENV, "model-a,model-b")
    fr.reset()
    _seed_measured_pair(armed_openvault)
    armed_openvault.reply("CONTROL")
    healthy = fr.complete(
        "learn-soft",
        [{"role": "user", "content": "control"}],
        max_tokens=4000,
    )
    assert healthy.stamp is not None
    assert healthy.stamp.requested == "model-b"
    assert healthy.stamp.route_source == ""
    healthy_body = dict(armed_openvault.chat_calls[-1]["body"])
    assert healthy_body["model"] == "model-b"
    assert healthy_body["max_tokens"] == 4000

    seen = _fail_after_first_select(monkeypatch)
    armed_openvault.reply(SERVED)
    out = fr.complete(
        "learn-soft",
        [{"role": "user", "content": PROMPT}],
        max_tokens=4000,
    )
    assert out.ok is True
    assert out.stamp is not None
    assert out.stamp.requested == healthy.stamp.requested
    assert out.stamp.route_source == healthy.stamp.route_source == ""
    body = armed_openvault.chat_calls[-1]["body"]
    assert body["model"] == healthy_body["model"]
    assert body["max_tokens"] == healthy_body["max_tokens"]
    assert "route_source" not in body
    assert seen["selects"] == 1


def test_unmeasured_first_model_is_not_served_without_a_stamp(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed read does not explore the unmeasured catalogue model.

    The call takes the full fallback path: stamp, clamp, and model auto.
    """
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    _only_models(armed_openvault, ["unmeasured-first", "other"])
    fr.reset()
    fr._init_file(fr.store_path())
    cap = fr.fallback_max_tokens()
    _fail_scoreboard_reads(monkeypatch)
    armed_openvault.reply(SERVED)
    out = fr.complete(
        "unmeasured",
        [{"role": "user", "content": PROMPT}],
        max_tokens=4000,
    )
    assert out.ok is True
    assert out.stamp is not None
    assert out.stamp.requested == "auto"
    assert out.stamp.route_source == "default_fallback"
    assert out.stamp.public()["route_source"] == "default_fallback"
    body = armed_openvault.chat_calls[-1]["body"]
    _assert_chat_body(body, max_tokens=cap)
    assert body["model"] == "auto"
    assert body["model"] != "unmeasured-first"


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "601"])
def test_fallback_cap_refuses_at_startup(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """0, -1, abc, and 601 raise at the startup check. They are not clamped."""
    monkeypatch.setenv(fr.FALLBACK_MAX_TOKENS_ENV, bad)
    with pytest.raises(fr.FreeRouteFallbackCapInvalid) as raised:
        fr.check_fallback_cap()
    message = str(raised.value)
    assert "FREEROUTE_FALLBACK_CAP_INVALID" in message
    assert fr.FALLBACK_MAX_TOKENS_ENV in message
    assert bad in message


@pytest.mark.parametrize("cap", [1, 600])
def test_fallback_cap_accepts_bounds(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
    cap: int,
) -> None:
    """1 and 600 are valid. A failed read clamps to that cap."""
    monkeypatch.setenv(fr.FALLBACK_MAX_TOKENS_ENV, str(cap))
    assert fr.check_fallback_cap() == cap
    fr.reset()
    fr._init_file(fr.store_path())
    _fail_scoreboard_reads(monkeypatch)
    armed_openvault.reply(SERVED)
    out = fr.complete("cap-bound", [{"role": "user", "content": PROMPT}], max_tokens=4000)
    assert out.stamp is not None
    assert out.stamp.route_source == "default_fallback"
    assert out.stamp.requested == "auto"
    _assert_chat_body(armed_openvault.chat_calls[-1]["body"], max_tokens=cap)


def test_outbound_body_keys_stay_on_openai_chat_allowlist(
    armed_openvault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct, complete_core, and crew bodies stay inside the chat allowlist.

    Healthy reads and failed reads both. A stamp field on the body goes red.
    """
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    fr.reset()
    fr._init_file(fr.store_path())
    cap = fr.fallback_max_tokens()

    def _healthy(kind: str) -> dict:
        armed_openvault.reply(SERVED)
        if kind == "direct":
            out = fr.complete("allow", [{"role": "user", "content": "h"}], max_tokens=4000)
            assert out.stamp is not None
            assert out.stamp.route_source == ""
        elif kind == "core":
            out = asyncio.run(
                complete_core("allow", [{"role": "user", "content": "h"}], max_tokens=2048)
            )
            assert out.stamp is not None
            assert out.stamp.route_source == ""
        else:
            crew = asyncio.run(crew_complete(prompt="h", purpose="think"))
            assert crew["ok"] is True
            assert crew["stamp"]["route_source"] == ""
        return armed_openvault.chat_calls[-1]["body"]

    _assert_chat_body(_healthy("direct"), max_tokens=4000)
    _assert_chat_body(_healthy("core"), max_tokens=2048)
    _assert_chat_body(_healthy("crew"), max_tokens=2048)

    tool = {
        "type": "function",
        "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}},
    }
    armed_openvault.reply(SERVED)
    acted = asyncio.run(crew_complete(prompt="use the tool", purpose="act", tools=[tool]))
    assert acted["ok"] is True
    act_body = armed_openvault.chat_calls[-1]["body"]
    assert "tools" in act_body
    assert act_body["tool_choice"] == "auto"
    _assert_chat_body(act_body, max_tokens=2048)

    local_hop = armed_openvault.hop("groq", 10)
    local_hop["served_local"] = True
    armed_openvault.hops = [local_hop]
    armed_openvault.catalogue = {"groq": ["model-a"]}
    fr.reset()
    monkeypatch.setenv(fr.LOCAL_ONLY_ENV, "1")
    armed_openvault.reply(SERVED, model="model-a")
    local = fr.complete("allow-local", [{"role": "user", "content": "local"}], max_tokens=4000)
    assert local.stamp is not None
    local_body = armed_openvault.chat_calls[-1]["body"]
    assert local_body["local_only"] is True
    _assert_chat_body(local_body, max_tokens=4000)
    monkeypatch.delenv(fr.LOCAL_ONLY_ENV, raising=False)

    _fail_scoreboard_reads(monkeypatch)

    def _miss(kind: str) -> dict:
        armed_openvault.reply(SERVED)
        if kind == "direct":
            out = fr.complete("allow", [{"role": "user", "content": "m"}], max_tokens=4000)
            assert out.stamp is not None
            assert out.stamp.route_source == "default_fallback"
            assert out.stamp.requested == "auto"
        elif kind == "core":
            out = asyncio.run(
                complete_core("allow", [{"role": "user", "content": "m"}], max_tokens=2048)
            )
            assert out.stamp is not None
            assert out.stamp.route_source == "default_fallback"
            assert out.stamp.requested == "auto"
        else:
            crew = asyncio.run(crew_complete(prompt="m", purpose="think"))
            assert crew["ok"] is True
            assert crew["stamp"]["route_source"] == "default_fallback"
            assert crew["stamp"]["requested"] == "auto"
        return armed_openvault.chat_calls[-1]["body"]

    for kind in ("direct", "core", "crew"):
        body = _miss(kind)
        _assert_chat_body(body, max_tokens=cap)
        assert body["model"] == "auto"


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
