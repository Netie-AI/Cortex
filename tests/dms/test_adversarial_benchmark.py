"""C10 — adversarial category gates (wrong==0 on gated categories)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "bench" / "golden" / "adversarial_baseline.json"


def _holding_pids_hint(exc: BaseException) -> str:
    """Best-effort PID extraction from DuckDB lock errors / Windows process list."""
    msg = str(exc)
    found: list[str] = []
    for m in re.finditer(r"\bPID\s*[:=]?\s*(\d+)\b", msg, flags=re.I):
        found.append(m.group(1))
    for m in re.finditer(r"\(PID\s+(\d+)\)", msg, flags=re.I):
        found.append(m.group(1))
    if not found:
        try:
            import subprocess

            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                errors="replace",
                timeout=5,
            )
            for line in out.splitlines():
                if ":8010" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit():
                        found.append(pid)
        except Exception:  # noqa: BLE001
            pass
    return ",".join(dict.fromkeys(found)) if found else "unknown"


@pytest.fixture(scope="module")
def adversarial_report():
    from bench.adversarial import run_benchmark

    try:
        return run_benchmark()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # A skipped suite cannot fail — lock means C10 never measured (importlinter-class gate).
        if "being used by another process" in msg or "Cannot open file" in msg:
            pids = _holding_pids_hint(exc)
            pytest.fail(
                "warehouse locked by a live Cortex process — stop the API holding "
                f"DuckDB before running C10 adversarial. holding_pid={pids}. "
                f"Original error: {msg[:240]}"
            )
        raise


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_adversarial_wrong_zero_on_gated(adversarial_report, baseline):
    assert baseline.get("wrong_must_be_zero") is True
    by_cat = adversarial_report["by_category"]
    for cat in baseline.get("gated_categories") or []:
        summary = by_cat.get(cat) or {"wrong": 0, "total": 0}
        wrong = int(summary.get("wrong") or 0)
        assert wrong == 0, f"{cat}: confidently wrong={wrong} items={summary}"


def test_value_normalization_category_present(adversarial_report):
    by_cat = adversarial_report["by_category"]
    assert "value_normalization" in by_cat
    assert by_cat["value_normalization"]["total"] >= 1
    assert by_cat["value_normalization"]["wrong"] == 0


def test_eleven_categories_declared():
    from bench.adversarial import CATEGORIES

    assert len(CATEGORIES) == 11
    assert "value_normalization" in CATEGORIES


def test_holding_pids_hint_parses_pid_token():
    assert "28008" in _holding_pids_hint(RuntimeError("File locked (PID 28008)"))
