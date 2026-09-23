"""KEV-CALIB (#247): outcome labels and the calibration report an operator reads.

Every test asserts the artifact a person sees: the JSONL lines written by the
KEV-LOG writer and read back, the report's stdout and exit code from a real
subprocess, and the JudgmentDecision the router serves after the report ran.
The report must refuse (``INSUFFICIENT``, non-zero) below n=300 or below 30
negatives, must never print ECE, Brier, T or a threshold in that case, and must
never change ``CORTEX_DECISION_ABSTAIN_THRESHOLD``.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from netie.decision import decision_log, labels
from netie.decision.decide import ABSTAIN_THRESHOLD_ENV, default_abstain_threshold
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "kev_calibration_report.py"
TIERS = ("T0", "T1", "T2", "T3")
SECRET = "PLANTED-PROMPT-SECRET-4d1c"


# ---------------------------------------------------------------------------
# synthetic log written through the real KEV-LOG writer
# ---------------------------------------------------------------------------


def _entry(
    i: int,
    *,
    p_served: float,
    tier: str = "T1",
    status: str = "ok",
    error_class: str | None = None,
    probabilities: dict[str, float] | None | str = "auto",
    backend: str | None = "rules-v0",
    run_id: str | None = None,
    node_id: str = "j1",
) -> dict:
    if probabilities == "auto":
        rest = (1.0 - p_served) / 3.0
        probabilities = {t: (p_served if t == tier else rest) for t in TIERS}
    return {
        "schema_version": decision_log.SCHEMA_VERSION,
        "ts": "2026-09-23T00:00:00+00:00",
        "run_id": run_id or f"run_{i}",
        "node_id": node_id,
        "request_type": "plain_review",
        "default_tier": tier,
        "max_tier": "T3",
        "tier": tier,
        "reason": "heuristic routing fallback",
        "backend": backend,
        "confidence": p_served if probabilities is not None else None,
        "probabilities": probabilities,
        "status": status,
        "error_class": error_class,
        "cost_myr": 0.0,
        "state_hash": decision_log.state_hash(
            {"content": f"{SECRET} {i}", "request_type": "plain_review"}
        ),
        "context_size": 10,
        "prior_tier_failures": 0,
        "user_tier_budget": "T3",
        "is_vip": False,
        "provider": "anthropic",
        "model": "m",
    }


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "engine" / "tier_decisions.jsonl"
    monkeypatch.setenv(decision_log.PATH_ENV, str(path))
    monkeypatch.delenv(decision_log.ENABLE_ENV, raising=False)
    decision_log.reset_write_failures()
    yield path
    decision_log.reset_write_failures()


def _write(log_file: Path, entries: list[dict]) -> list[dict]:
    for e in entries:
        assert decision_log.append(e), decision_log.last_write_error()
    assert decision_log.write_failures() == 0
    back = decision_log.read_entries(log_file)
    assert len(back) == len(entries)
    return back


def _synthetic(n: int, *, p_served: float, negatives: int, seed: int = 11) -> list[dict]:
    """``n`` served-T1 rows at ``p_served`` with exactly ``negatives`` adapter failures."""
    rng = random.Random(seed)
    flags = [True] * negatives + [False] * (n - negatives)
    rng.shuffle(flags)
    out = []
    for i, is_negative in enumerate(flags):
        if is_negative:
            out.append(_entry(i, p_served=p_served, status="error", error_class="ValueError"))
        else:
            out.append(_entry(i, p_served=p_served))
    return out


def _run_report(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _kv(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line and " " not in line.split("=", 1)[0]:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _assert_no_metrics(stdout: str) -> None:
    for banned in ("ece_", "brier", "T=", "implied_threshold", "automatable_share"):
        assert banned not in stdout, f"{banned!r} printed on an INSUFFICIENT report:\n{stdout}"


# ---------------------------------------------------------------------------
# below n=300: refuse
# ---------------------------------------------------------------------------


def test_below_n300_prints_insufficient_and_exits_nonzero(log_file: Path):
    _write(log_file, _synthetic(120, p_served=0.7, negatives=40))

    proc = _run_report("--log", str(log_file))

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert proc.returncode == 2
    kv = _kv(proc.stdout)
    assert kv["n"] == "120"
    assert "class_balance sufficient=80 insufficient=40" in proc.stdout
    assert "INSUFFICIENT: n=120 < 300 labelled rows; no threshold claim" in proc.stdout
    _assert_no_metrics(proc.stdout)
    assert SECRET not in proc.stdout


def test_missing_log_is_insufficient_not_a_crash(tmp_path: Path):
    proc = _run_report("--log", str(tmp_path / "nope.jsonl"))
    assert proc.returncode == 2
    assert "n=0" in proc.stdout
    assert "INSUFFICIENT" in proc.stdout
    _assert_no_metrics(proc.stdout)


# ---------------------------------------------------------------------------
# zero negatives: refuse on the negatives rule even with n >= 300
# ---------------------------------------------------------------------------


def test_zero_negatives_prints_insufficient_on_negatives_rule(log_file: Path):
    _write(log_file, _synthetic(400, p_served=0.9, negatives=0))

    proc = _run_report("--log", str(log_file))

    assert proc.returncode == 2
    kv = _kv(proc.stdout)
    assert kv["n"] == "400"
    assert "class_balance sufficient=400 insufficient=0" in proc.stdout
    assert "INSUFFICIENT: negatives=0 < 30; no threshold claim" in proc.stdout
    _assert_no_metrics(proc.stdout)


def test_29_negatives_still_refuses(log_file: Path):
    _write(log_file, _synthetic(400, p_served=0.9, negatives=29))
    proc = _run_report("--log", str(log_file))
    assert proc.returncode == 2
    assert "INSUFFICIENT: negatives=29 < 30" in proc.stdout
    _assert_no_metrics(proc.stdout)


# ---------------------------------------------------------------------------
# enough data with a known miscalibration: T moves the right way, numbers print
# ---------------------------------------------------------------------------


def test_overconfident_log_fits_temperature_above_one(log_file: Path):
    # Says 0.95 for the served tier, is right 80% of the time: overconfident, T > 1.
    _write(log_file, _synthetic(400, p_served=0.95, negatives=80))

    proc = _run_report("--log", str(log_file))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    kv = _kv(proc.stdout)
    assert kv["n"] == "400"
    assert "class_balance sufficient=320 insufficient=80" in proc.stdout
    assert "pseudo-logits" in kv["temperature_note"]
    t = float(kv["T"])
    assert t > 1.0, proc.stdout
    ece_raw, ece_scaled = float(kv["ece_raw"]), float(kv["ece_scaled"])
    assert ece_raw == pytest.approx(0.15, abs=0.01)
    assert ece_scaled < ece_raw
    assert 0.0 <= float(kv["brier_raw"]) <= 1.0
    assert float(kv["brier_scaled"]) < float(kv["brier_raw"])
    for budget in ("0.02", "0.05", "0.10"):
        assert f"automatable_share budget={budget} share=" in proc.stdout
    assert "implied_threshold=" in proc.stdout
    assert "INSUFFICIENT" not in proc.stdout
    assert "CORTEX_DECISION_ABSTAIN_THRESHOLD untouched" in proc.stdout


def test_underconfident_log_fits_temperature_below_one(log_file: Path):
    # Says 0.6 for the served tier, is right 90% of the time: underconfident, T < 1.
    _write(log_file, _synthetic(400, p_served=0.6, negatives=40))

    proc = _run_report("--log", str(log_file))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    kv = _kv(proc.stdout)
    assert float(kv["T"]) < 1.0, proc.stdout
    assert float(kv["ece_scaled"]) < float(kv["ece_raw"])


# ---------------------------------------------------------------------------
# infrastructure errors are excluded and counted, never labelled 0
# ---------------------------------------------------------------------------


def test_infra_errors_are_excluded_and_counted(log_file: Path):
    entries = _synthetic(100, p_served=0.8, negatives=10)
    infra = [
        ("CostCeilingExceeded", 3),
        ("WorkflowCostCeilingExceeded", 2),
        ("RedactionFailed", 4),
        ("TimeoutError", 5),
        ("ConnectTimeout", 1),
    ]
    i = 1000
    for cls, count in infra:
        for _ in range(count):
            entries.append(_entry(i, p_served=0.8, status="error", error_class=cls))
            i += 1
    back = _write(log_file, entries)

    labelled = labels.build_labels(back)
    assert labelled.total_entries == 115
    assert labelled.n == 100
    assert labelled.negatives == 10
    for cls, count in infra:
        assert labelled.excluded[f"infra:{cls}"] == count
    assert sum(labelled.excluded.values()) == 15
    assert labelled.n + sum(labelled.excluded.values()) == labelled.total_entries
    assert not any(r.label == 0 and r.run_id.startswith("run_10") for r in labelled.rows)

    proc = _run_report("--log", str(log_file))
    assert proc.returncode == 2
    kv = _kv(proc.stdout)
    assert kv["n"] == "100"
    for cls, count in infra:
        assert f"excluded infra:{cls}={count}" in proc.stdout
    assert "class_balance sufficient=90 insufficient=10" in proc.stdout


def test_rows_without_probabilities_are_excluded_not_guessed(log_file: Path):
    entries = _synthetic(10, p_served=0.8, negatives=2)
    entries.append(_entry(500, p_served=0.8, probabilities=None, backend="kev-http"))
    entries.append(_entry(501, p_served=0.8, probabilities={"T0": 1.0}, tier="T1"))
    entries.append(_entry(502, p_served=0.8, status="weird"))
    entries.append({**_entry(503, p_served=0.8), "run_id": ""})
    back = _write(log_file, entries)

    labelled = labels.build_labels(back)
    assert labelled.n == 10
    assert labelled.excluded[labels.EXCLUDE_NO_PROBABILITIES] == 1
    assert labelled.excluded[labels.EXCLUDE_NO_SERVED_PROBABILITY] == 1
    assert labelled.excluded[labels.EXCLUDE_UNKNOWN_STATUS] == 1
    assert labelled.excluded[labels.EXCLUDE_MISSING_KEY] == 1

    proc = _run_report("--log", str(log_file))
    assert proc.returncode == 2
    assert f"excluded {labels.EXCLUDE_NO_PROBABILITIES}=1" in proc.stdout
    assert f"excluded {labels.EXCLUDE_UNKNOWN_STATUS}=1" in proc.stdout


def test_provider_availability_errors_are_infra_not_label_zero(log_file: Path):
    """litellm raises these from the adapters; an outage window must not become false negatives."""
    provider = [
        "RateLimitError",
        "ServiceUnavailableError",
        "InternalServerError",
        "BadGatewayError",
        "APIError",
        "APIConnectionError",
        "OverloadedError",
        "HTTPStatusError",
        "Timeout",
    ]
    for cls in provider:
        assert labels.is_infra_error(cls), cls
    # content / validation failures stay label 0
    for cls in (
        "ValueError",
        "JSONSchemaValidationError",
        "ContextWindowExceededError",
        "BadRequestError",
    ):
        assert not labels.is_infra_error(cls), cls

    entries = _synthetic(50, p_served=0.8, negatives=5)
    i = 2000
    for cls in provider:
        for _ in range(4):
            entries.append(_entry(i, p_served=0.8, status="error", error_class=cls))
            i += 1
    back = _write(log_file, entries)

    labelled = labels.build_labels(back)
    assert labelled.total_entries == 50 + 4 * len(provider)
    assert labelled.n == 50
    assert labelled.negatives == 5
    for cls in provider:
        assert labelled.excluded[f"infra:{cls}"] == 4
    assert sum(labelled.excluded.values()) == 4 * len(provider)

    proc = _run_report("--log", str(log_file))
    assert proc.returncode == 2
    assert "class_balance sufficient=45 insufficient=5" in proc.stdout
    assert "excluded infra:RateLimitError=4" in proc.stdout
    assert "excluded infra:ServiceUnavailableError=4" in proc.stdout


# ---------------------------------------------------------------------------
# joins: ledger status overrides the log's own status; scoreboard predicates
# ---------------------------------------------------------------------------


def _ledger_row(run_id: str, node_id: str, status: str, error: str | None = None) -> dict:
    return {"run_id": run_id, "node_id": node_id, "status": status, "error": error}


def _write_ledger(path: Path, rows: list[dict]) -> Path:
    import json

    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_multi_step_node_pairs_each_attempt_with_its_own_ledger_record(
    log_file: Path, tmp_path: Path
):
    """agent_task calls invoke_routed_completion once per step under the same (run_id, node_id).

    Three successful steps followed by a ReadTimeout must yield three 1s and one
    infra exclusion, never three 0s taken from the last ledger record.
    """
    steps = [
        _entry(0, p_served=0.7, run_id="r1", node_id="agent"),
        _entry(1, p_served=0.7, run_id="r1", node_id="agent"),
        _entry(2, p_served=0.7, run_id="r1", node_id="agent"),
        _entry(
            3, p_served=0.7, run_id="r1", node_id="agent", status="error", error_class="ReadTimeout"
        ),
    ]
    back = _write(log_file, steps)
    ledger_rows = [
        _ledger_row("r1", "agent", "ok"),
        _ledger_row("r1", "agent", "ok"),
        _ledger_row("r1", "agent", "ok"),
        _ledger_row("r1", "agent", "error", "timed out"),
    ]

    labelled = labels.build_labels(back, ledger_records=ledger_rows)
    assert labelled.total_entries == 4
    assert labelled.n == 3
    assert labelled.negatives == 0
    assert [(r.label, r.source) for r in labelled.rows] == [(1, "ledger_status")] * 3
    assert labelled.excluded == {"infra:ReadTimeout": 1}

    proc = _run_report(
        "--log",
        str(log_file),
        "--ledger-jsonl",
        str(_write_ledger(tmp_path / "ledger.jsonl", ledger_rows)),
    )
    assert proc.returncode == 2
    assert "class_balance sufficient=3 insufficient=0" in proc.stdout
    assert "excluded infra:ReadTimeout=1" in proc.stdout


def test_failure_then_success_keeps_the_failure(log_file: Path, tmp_path: Path):
    """A genuine failure followed by a successful retry must not be erased by the last record."""
    back = _write(
        log_file,
        [
            _entry(
                0,
                p_served=0.7,
                run_id="r1",
                node_id="agent",
                status="error",
                error_class="ValueError",
            ),
            _entry(1, p_served=0.7, run_id="r1", node_id="agent"),
        ],
    )
    ledger_rows = [
        _ledger_row("r1", "agent", "error", "bad output"),
        _ledger_row("r1", "agent", "ok"),
    ]

    labelled = labels.build_labels(back, ledger_records=ledger_rows)
    assert [(r.label, r.source) for r in labelled.rows] == [
        (0, "ledger_status"),
        (1, "ledger_status"),
    ]
    assert labelled.negatives == 1

    proc = _run_report(
        "--log",
        str(log_file),
        "--ledger-jsonl",
        str(_write_ledger(tmp_path / "ledger.jsonl", ledger_rows)),
    )
    assert proc.returncode == 2
    assert "class_balance sufficient=1 insufficient=1" in proc.stdout


def test_ledger_infra_error_text_is_excluded_not_label_zero(log_file: Path, tmp_path: Path):
    """The ledger holds str(exc); an ok log row paired with a ledger timeout is infra, not 0."""
    back = _write(
        log_file,
        [
            _entry(0, p_served=0.7, run_id="r1", node_id="n"),
            _entry(1, p_served=0.7, run_id="r2", node_id="n"),
            _entry(2, p_served=0.7, run_id="r3", node_id="n"),
        ],
    )
    ledger_rows = [
        _ledger_row("r1", "n", "error", "ReadTimeout: timed out"),
        _ledger_row("r2", "n", "error", "litellm.RateLimitError: 429 Too Many Requests"),
        _ledger_row("r3", "n", "error", "bad output on line 500"),
    ]
    labelled = labels.build_labels(back, ledger_records=ledger_rows)
    assert labelled.excluded[labels.EXCLUDE_INFRA_LEDGER] == 2
    assert [(r.run_id, r.label) for r in labelled.rows] == [("r3", 0)]

    proc = _run_report(
        "--log",
        str(log_file),
        "--ledger-jsonl",
        str(_write_ledger(tmp_path / "ledger.jsonl", ledger_rows)),
    )
    assert proc.returncode == 2
    assert f"excluded {labels.EXCLUDE_INFRA_LEDGER}=2" in proc.stdout
    assert "class_balance sufficient=0 insufficient=1" in proc.stdout


def test_mismatched_ledger_count_is_ambiguous_not_last_status(log_file: Path, tmp_path: Path):
    """Two log rows, one ledger record: nothing says which attempt it belongs to."""
    back = _write(
        log_file,
        [
            _entry(0, p_served=0.7, run_id="r1", node_id="agent"),
            _entry(1, p_served=0.7, run_id="r1", node_id="agent"),
            _entry(2, p_served=0.7, run_id="r9", node_id="agent"),
        ],
    )
    ledger_rows = [_ledger_row("r1", "agent", "error", "bad output")]

    labelled = labels.build_labels(back, ledger_records=ledger_rows)
    assert labelled.excluded[labels.EXCLUDE_AMBIGUOUS_LEDGER_JOIN] == 2
    assert [(r.run_id, r.label, r.source) for r in labelled.rows] == [("r9", 1, "log_status")]

    proc = _run_report(
        "--log",
        str(log_file),
        "--ledger-jsonl",
        str(_write_ledger(tmp_path / "ledger.jsonl", ledger_rows)),
    )
    assert proc.returncode == 2
    assert f"excluded {labels.EXCLUDE_AMBIGUOUS_LEDGER_JOIN}=2" in proc.stdout
    assert "class_balance sufficient=1 insufficient=0" in proc.stdout


def test_cost_ceiling_rows_do_not_consume_a_ledger_record(log_file: Path):
    """CostCeilingExceeded raises before ledger.add, so its log row has no ledger record."""
    back = _write(
        log_file,
        [
            _entry(0, p_served=0.7, run_id="r1", node_id="agent"),
            _entry(
                1,
                p_served=0.7,
                run_id="r1",
                node_id="agent",
                status="error",
                error_class="CostCeilingExceeded",
            ),
            _entry(2, p_served=0.7, run_id="r1", node_id="agent"),
        ],
    )
    ledger_rows = [
        _ledger_row("r1", "agent", "ok"),
        _ledger_row("r1", "agent", "error", "bad output"),
    ]
    labelled = labels.build_labels(back, ledger_records=ledger_rows)
    assert labelled.excluded == {"infra:CostCeilingExceeded": 1}
    assert [(r.label, r.source) for r in labelled.rows] == [
        (1, "ledger_status"),
        (0, "ledger_status"),
    ]


def test_ledger_status_overrides_log_status(log_file: Path, tmp_path: Path):
    back = _write(
        log_file, [_entry(1, p_served=0.8, status="ok"), _entry(2, p_served=0.8, status="ok")]
    )
    ledger_rows = [{"run_id": "run_1", "node_id": "j1", "status": "error", "error": "bad output"}]

    labelled = labels.build_labels(back, ledger_records=ledger_rows)
    by_run = {r.run_id: r for r in labelled.rows}
    assert by_run["run_1"].label == 0
    assert by_run["run_1"].source == "ledger_status"
    assert by_run["run_2"].label == 1
    assert by_run["run_2"].source == "log_status"

    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        '{"run_id":"run_1","node_id":"j1","status":"error","error":"bad output"}\n'
    )
    proc = _run_report("--log", str(log_file), "--ledger-jsonl", str(ledger_path))
    assert proc.returncode == 2
    assert "class_balance sufficient=1 insufficient=1" in proc.stdout


def test_scoreboard_predicates_label_ok_rows(
    log_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from netie.execution import scoreboard

    db = tmp_path / "scoreboard.db"
    monkeypatch.setattr(scoreboard, "DB_PATH", db)
    scoreboard.init()
    scoreboard.record_run("run_1", "fam", "preset", predicates_pass=False)
    scoreboard.record_run("run_2", "fam", "preset", predicates_pass=True)
    scoreboard.record_run("run_2", "fam", "preset", predicates_pass=True)

    back = _write(
        log_file,
        [_entry(1, p_served=0.8), _entry(2, p_served=0.8), _entry(3, p_served=0.8)],
    )
    predicates = labels.scoreboard_predicates(db)
    assert predicates == {"run_1": False, "run_2": True}
    labelled = labels.build_labels(back, predicates=predicates)
    by_run = {r.run_id: r for r in labelled.rows}
    assert by_run["run_1"].label == 0 and by_run["run_1"].source == "scoreboard_predicates"
    assert by_run["run_2"].label == 1 and by_run["run_2"].source == "scoreboard_predicates"
    assert by_run["run_3"].label == 1 and by_run["run_3"].source == "log_status"

    proc = _run_report("--log", str(log_file), "--scoreboard-db", str(db))
    assert proc.returncode == 2
    assert "class_balance sufficient=2 insufficient=1" in proc.stdout

    assert labels.scoreboard_predicates(tmp_path / "absent.db") == {}


# ---------------------------------------------------------------------------
# the report never moves the threshold or the served decision
# ---------------------------------------------------------------------------


def test_report_never_changes_threshold_or_served_decision(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
):
    _write(log_file, _synthetic(400, p_served=0.95, negatives=80))
    monkeypatch.setenv(ABSTAIN_THRESHOLD_ENV, "0.42")
    before_threshold = default_abstain_threshold()
    req = JudgmentRequest(request_type="chat", content="hello there")
    before = JudgmentModel().decide(req)

    proc = _run_report("--log", str(log_file))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "config_written=none" in proc.stdout

    assert os.environ[ABSTAIN_THRESHOLD_ENV] == "0.42"
    assert default_abstain_threshold() == before_threshold == 0.42
    after = JudgmentModel().decide(req)
    assert after == before
    assert after.tier.value == "T1" and after.confidence == pytest.approx(0.7)
