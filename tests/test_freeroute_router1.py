"""Cortex #269 ROUTER-1: cost store, shadow/held-out/benchmark filter, baseline.

Stubbed OpenVault only. No live key, no network, no skips.
The held-out/shadow pick test must fail on the ca801a23 _rows query.
"""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from CortexOS.integrations import freeroute as fr

ROOT = Path(__file__).resolve().parents[1]
OLD_ROWS_SQL = "SELECT * FROM routes WHERE task = ? ORDER BY ts DESC LIMIT ?"


def _sql_ok(text: str) -> bool:
    return "select" in text.lower() and "from" in text.lower()


def _only_groq_models(fake, models: list[str]) -> None:
    fake.hops = [fake.hop("groq", 10)]
    fake.catalogue = {"groq": list(models)}


def _seed_product_honest_beats_poison(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["poison", "honest"])
    msgs = [{"role": "user", "content": "q"}]
    for _ in range(2):
        out = fr.complete("t", msgs, accept=_sql_ok, pin="poison")
        fr.note_verdict(out.stamp, "gate_fail")
    for _ in range(2):
        out = fr.complete("t", msgs, accept=_sql_ok, pin="honest")
        fr.note_verdict(out.stamp, "gate_pass")


def _poison_nonlearning_rows(armed_openvault) -> None:
    msgs = [{"role": "user", "content": "q"}]
    with fr.journal(shadow=True):
        for _ in range(8):
            out = fr.complete("t", msgs, accept=_sql_ok, pin="poison")
            fr.note_verdict(out.stamp, "gate_pass")
    with fr.journal(split="heldout"):
        for _ in range(8):
            out = fr.complete("t", msgs, accept=_sql_ok, pin="poison")
            fr.note_verdict(out.stamp, "gate_pass")
    with fr.journal(split="benchmark"):
        for _ in range(8):
            out = fr.complete("t", msgs, accept=_sql_ok, pin="poison")
            fr.note_verdict(out.stamp, "gate_pass")


def _unfiltered_rows(task: str) -> list[sqlite3.Row]:
    path = fr.store_path()
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        return list(
            con.execute(OLD_ROWS_SQL, (task, fr.SCORE_WINDOW * fr.MAX_CANDIDATES))
        )
    finally:
        con.close()


def test_heldout_shadow_benchmark_rows_do_not_change_pick(armed_openvault) -> None:
    """Fails on ca801a23: old _rows/_stats trained pick on shadow and held-out."""
    _seed_product_honest_beats_poison(armed_openvault)
    before = fr.pick("t", fr.arming())
    assert before.requested == "honest"
    _poison_nonlearning_rows(armed_openvault)
    after = fr.pick("t", fr.arming())
    assert after.requested == "honest"
    assert after.reason == before.reason or "measured best validity" in after.reason


def test_old_unfiltered_query_would_train_on_poison(armed_openvault, monkeypatch) -> None:
    """Shows the old SQL includes non-learning rows that flip pick()."""
    _seed_product_honest_beats_poison(armed_openvault)
    _poison_nonlearning_rows(armed_openvault)
    assert fr.pick("t", fr.arming()).requested == "honest"
    old = _unfiltered_rows("t")
    assert any(int(r["shadow"]) == 1 for r in old)
    splits = {str(r["split"]) for r in old}
    assert "heldout" in splits
    assert "benchmark" in splits
    monkeypatch.setattr(fr, "_rows", lambda task: old)
    poisoned = fr.pick("t", fr.arming())
    assert poisoned.requested == "poison"


def test_learning_filter_is_wired_into_rows() -> None:
    src = inspect.getsource(fr._rows)
    assert "_LEARNING_FILTER_SQL" in src
    assert "shadow = 0" in fr._LEARNING_FILTER_SQL
    assert "heldout" in fr._LEARNING_FILTER_SQL
    assert "benchmark" in fr._LEARNING_FILTER_SQL
    assert "SELECT * FROM routes WHERE task = ? ORDER BY ts DESC" not in src.replace(
        "\n", " "
    )


def test_token_usage_is_stored_and_mean_cost_is_exposed(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["m1"])

    def replay(method, path, **kwargs):  # noqa: ARG001
        return 200, {
            "model": "m1",
            "choices": [{"message": {"role": "assistant", "content": "SELECT 1 FROM t"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    with fr.use_transport(replay, "replay:tokens"):
        out = fr.complete("t", [{"role": "user", "content": "q"}], accept=_sql_ok)
    assert out.usage.get("total_tokens") == 18
    con = sqlite3.connect(str(fr.store_path()))
    row = con.execute(
        "select prompt_tokens, completion_tokens, total_tokens, split from routes"
    ).fetchone()
    assert row == (11, 7, 18, "product")
    stats = fr._stats("t", ["m1"])["m1"]
    assert stats.mean_cost == 18.0
    status = fr.public_status("t")
    assert status["measured"]["m1"]["mean_cost"] == 18.0
    assert status["masking_state"] == "off"
    assert status["measured_excludes"] == ["shadow", "heldout", "benchmark"]
    picked = fr.pick("t", fr.arming())
    assert "cost" not in picked.reason
    assert "mean_cost" not in picked.reason


def test_additive_migration_keeps_old_rows(armed_openvault, tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy_routes.db"
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(path))
    fr.reset()
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE routes (
            call_id TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            requested TEXT NOT NULL,
            served TEXT NOT NULL,
            status INTEGER NOT NULL,
            usable INTEGER NOT NULL,
            scored INTEGER NOT NULL,
            verdict TEXT,
            latency_ms REAL NOT NULL,
            impl TEXT NOT NULL,
            shadow INTEGER NOT NULL,
            ts REAL NOT NULL
        )
        """
    )
    con.execute(
        "INSERT INTO routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("old1", "t", "legacy", "legacy", 200, 1, 1, "gate_pass", 10.0, "openvault-freeroute", 0, 1.0),
    )
    con.commit()
    con.close()
    _only_groq_models(armed_openvault, ["m1"])
    fr.complete("t", [{"role": "user", "content": "q"}], accept=_sql_ok)
    con = sqlite3.connect(str(path))
    cols = {row[1] for row in con.execute("PRAGMA table_info(routes)")}
    assert "prompt_tokens" in cols
    assert "completion_tokens" in cols
    assert "total_tokens" in cols
    assert "split" in cols
    assert con.execute("select count(*) from routes").fetchone()[0] == 2
    assert con.execute("select requested from routes where call_id='old1'").fetchone()[0] == (
        "legacy"
    )
    apply_src = inspect.getsource(fr._apply_schema)
    assert "DROP TABLE" not in apply_src
    assert "DROP COLUMN" not in apply_src


def test_learn_state_reports_default_vs_env(monkeypatch) -> None:
    monkeypatch.delenv("CORTEX_FREEROUTE_LEARN", raising=False)
    state = fr.learn_state()
    assert state["learn_enabled"] is True
    assert state["learn_source"] == "default"
    monkeypatch.setenv("CORTEX_FREEROUTE_LEARN", "0")
    off = fr.learn_state()
    assert off["learn_enabled"] is False
    assert off["learn_source"] == "env"


def test_router_fingerprint_never_infers_served_from_requested(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["openai/gpt-oss-120b"])
    out = fr.complete(
        "t",
        [{"role": "user", "content": "q"}],
        accept=_sql_ok,
        pin="deepseek-v4-pro",
        pin_source="DMS_L2_MODEL",
    )
    assert out.stamp.requested == "deepseek-v4-pro"
    assert out.stamp.served == "openai/gpt-oss-120b"
    fp = fr.router_fingerprint()
    assert fp["served_provider"] is None
    assert fp["served_model"] is None
    assert fp["served_local"] is False
    assert "272" in fp["served_reason"]
    dumped = json.dumps(fp)
    assert "deepseek-v4-pro" not in dumped
    assert "gpt-oss-120b" not in dumped
    assert fp["masking_state"] == "off"
    assert fp["learn_source"] in {"env", "default"}
    assert fp["route_store_id"]


def test_baseline_refuses_when_learn_unset() -> None:
    env = os.environ.copy()
    env.pop("CORTEX_FREEROUTE_LEARN", None)
    env["PYTHONPATH"] = str(ROOT)
    got = subprocess.run(
        [sys.executable, "-m", "CortexOS.integrations.freeroute_baseline"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        check=False,
    )
    assert got.returncode != 0
    assert "CORTEX_FREEROUTE_LEARN" in (got.stderr + got.stdout)
    assert "refuses" in (got.stderr + got.stdout).lower() or "unset" in (
        got.stderr + got.stdout
    ).lower()


_REFUSAL_TESTS = (
    "test_heldout_shadow_benchmark_rows_do_not_change_pick",
    "test_baseline_refuses_when_unarmed_and_records_reason",
    "test_baseline_refuses_when_spendable_hops_zero_and_records_reason",
    "test_baseline_records_setup_without_inventing_numbers_when_armed",
    "test_baseline_refuses_when_learn_unset",
)


def test_refusal_tests_have_no_skip_or_xfail_markers() -> None:
    """CI skip/xfail on these names is Formal NOT GREEN (comment 5829080661)."""
    import tests.test_freeroute_router1 as mod

    src = Path(__file__).read_text(encoding="utf-8")
    for name in _REFUSAL_TESTS:
        fn = getattr(mod, name)
        raw = getattr(fn, "pytestmark", [])
        marks = list(raw) if isinstance(raw, list) else [raw]
        kinds = {getattr(m, "name", "") for m in marks}
        assert "skip" not in kinds, name
        assert "skipif" not in kinds, name
        assert "xfail" not in kinds, name
        block = src.split(f"def {name}", 1)[1].split("\ndef ", 1)[0]
        assert "pytest.skip" not in block, name
        assert "pytest.xfail" not in block, name


def test_baseline_refuses_when_unarmed_and_records_reason(
    armed_openvault, monkeypatch, tmp_path
) -> None:
    """Stubbed OpenVault, sealed vault, no live key. Must not skip."""
    from CortexOS.integrations import freeroute_baseline as bl

    monkeypatch.setenv("CORTEX_FREEROUTE_LEARN", "0")
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "baseline.db"))
    armed_openvault.sealed = True
    fr.reset()
    body = bl.evaluate()
    assert body["recorded"] is False
    assert body["baseline_recorded"] is False
    assert body["armed"] is False
    assert body["spendable_hops"] == 0
    assert "sealed" in body["arming_reason"].lower()
    assert body["numbers"] is None
    assert body["live_baseline_counts"] is False
    assert armed_openvault.status_calls, "must probe the stand-in OpenVault"
    env = os.environ.copy()
    env["CORTEX_FREEROUTE_LEARN"] = "0"
    env["CORTEX_FREEROUTE"] = "0"
    env["CORTEX_FREEROUTE_SCOREBOARD"] = str(tmp_path / "baseline-cli.db")
    env["PYTHONPATH"] = str(ROOT)
    got = subprocess.run(
        [sys.executable, "-m", "CortexOS.integrations.freeroute_baseline"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        check=False,
    )
    assert got.returncode != 0
    cli = json.loads(got.stdout)
    assert cli["recorded"] is False
    assert cli["armed"] is False
    assert cli["spendable_hops"] == 0
    assert cli["arming_reason"]
    assert "arming_reason:" in got.stderr


def test_baseline_refuses_when_spendable_hops_zero_and_records_reason(
    fake_openvault, monkeypatch, tmp_path
) -> None:
    """Stand-in transport installed. Arming result stubbed: do not change arming()."""
    from CortexOS.integrations import freeroute_baseline as bl

    monkeypatch.setenv("CORTEX_FREEROUTE_LEARN", "0")
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "baseline.db"))
    stub = fr.Arming(
        armed=True,
        reason="armed: 1 pooled keys, 0 spendable hops at http://127.0.0.1:5000 as loopback",
        url="http://127.0.0.1:5000",
        sealed=False,
        pooled_keys=1,
        spendable_hops=0,
    )
    monkeypatch.setattr(fr, "arming", lambda **k: stub)
    body = bl.evaluate()
    assert body["recorded"] is False
    assert body["armed"] is True
    assert body["spendable_hops"] == 0
    assert body["arming_reason"] == stub.reason
    assert body["numbers"] is None
    assert "spendable hop" in (body.get("refuse_reason") or "").lower()


def test_baseline_records_setup_without_inventing_numbers_when_armed(
    armed_openvault, monkeypatch, tmp_path
) -> None:
    from CortexOS.integrations import freeroute_baseline as bl

    monkeypatch.setenv("CORTEX_FREEROUTE_LEARN", "0")
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "baseline.db"))
    fr.reset()
    arm = fr.arming(fresh=True)
    assert arm.armed is True
    assert arm.spendable_hops > 0
    body = bl.evaluate()
    assert body["recorded"] is True
    assert body["armed"] is True
    assert body["spendable_hops"] > 0
    assert body["arming_reason"]
    assert "armed" in body["arming_reason"].lower()
    assert body["masking_state"] == "off"
    assert body["comparable_with_masking_on"] is False
    assert body["numbers"] is None
    assert body["coverage"] is None
    assert body["wrong"] is None
    assert body["invented_numbers"] is False
    assert body["live_baseline_counts"] is False
    assert body["learn_source"] == "env"
    assert body["learn_enabled"] is False
    assert "never be compared" in body["masking_compare"]
    assert "#266" in body["plan_source_from"]
    assert armed_openvault.status_calls, "must probe the stand-in OpenVault"


def test_baseline_row_plan_source_from_stamp_not_labels() -> None:
    from CortexOS.integrations import freeroute_baseline as bl

    assert (
        bl.row_plan_source(
            {
                "plan_source": "ontology_plan",
                "route": {"label": "deepseek", "model": "deepseek-chat"},
            }
        )
        == "ontology_plan"
    )
    assert bl.row_plan_source({"plan_source": "other", "badge": "governed_metric"}) == (
        "other"
    )
    assert bl.row_plan_source({"route": {"label": "deepseek", "model": "deepseek-chat"}}) == (
        "other"
    )
    assert bl.row_plan_source({"plan_source": "ontology_plan", "mode": "ontology_plan"}) == (
        "ontology_plan"
    )
