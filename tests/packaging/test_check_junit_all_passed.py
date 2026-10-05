"""Tests for the TRUST-05 junit all-passed guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_junit_all_passed.py"


def _junit(tmp_path: Path, cases: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="pytest">'
        f"{cases}</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _passed(name: str) -> str:
    return f'<testcase classname="tests.proof" name="{name}" time="0.01"/>'


def _skipped(name: str) -> str:
    return (
        f'<testcase classname="tests.proof" name="{name}" time="0">'
        '<skipped type="pytest.skip" message="DSN not set">skip</skipped>'
        "</testcase>"
    )


def _failed(name: str) -> str:
    return (
        f'<testcase classname="tests.proof" name="{name}" time="0">'
        '<failure message="assertion failed">failure</failure>'
        "</testcase>"
    )


def _errored(name: str) -> str:
    return (
        f'<testcase classname="tests.proof" name="{name}" time="0">'
        '<error message="fixture failed">error</error>'
        "</testcase>"
    )


def _run(junit: Path, expected: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--junit",
            str(junit),
            "--expect",
            expected,
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
        check=False,
    )


def test_all_expected_passed_exits_zero(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, _passed("test_a") + _passed("test_b")), "test_a,test_b")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: all 2 tests PASSED" in result.stdout
    assert "FAIL" not in result.stdout + result.stderr


def test_one_skipped_exits_one_and_prints_name(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, _passed("test_a") + _skipped("test_b")), "test_a,test_b")

    assert result.returncode == 1
    assert "skipped=test_b" in result.stdout
    assert "FAIL" in result.stderr


def test_one_missing_exits_one_and_prints_name(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, _passed("test_a")), "test_a,test_b")

    assert result.returncode == 1
    assert "missing=test_b" in result.stdout


def test_one_failure_exits_one_and_prints_name(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, _passed("test_a") + _failed("test_b")), "test_a,test_b")

    assert result.returncode == 1
    assert "failed=test_b" in result.stdout


def test_one_error_exits_one_and_prints_name(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, _passed("test_a") + _errored("test_b")), "test_a,test_b")

    assert result.returncode == 1
    assert "errored=test_b" in result.stdout


def test_empty_suite_exits_one_and_prints_missing_name(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, ""), "test_a")

    assert result.returncode == 1
    assert "empty suite" in result.stdout
    assert "missing=test_a" in result.stdout


def test_missing_junit_exits_one_and_prints_missing_name(tmp_path: Path) -> None:
    result = _run(tmp_path / "missing.xml", "test_a")

    assert result.returncode == 1
    assert "cannot read junit file" in result.stdout
    assert "missing=test_a" in result.stdout


def test_unexpected_skip_also_fails_closed(tmp_path: Path) -> None:
    result = _run(_junit(tmp_path, _passed("test_a") + _skipped("test_new")), "test_a")

    assert result.returncode == 1
    assert "skipped=test_new" in result.stdout
