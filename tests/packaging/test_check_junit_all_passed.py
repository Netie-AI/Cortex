"""TRUST-05 (#261): the junit all-passed guard must be able to fail, and must not
fail a green proof.

Every case runs the real script as a subprocess and asserts its exit code and
printed output, which is exactly what the RLS Proof CI job consumes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_junit_all_passed.py"


def _junit(tmp_path: Path, cases: str, name: str = "junit.xml") -> Path:
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="pytest">' + cases + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _passed(name: str) -> str:
    return f'<testcase classname="tests.m" name="{name}" time="0.01"/>'


def _skipped(name: str) -> str:
    return (
        f'<testcase classname="tests.m" name="{name}" time="0.0">'
        '<skipped type="pytest.skip" message="DSN not set">skip</skipped></testcase>'
    )


def _failed(name: str) -> str:
    return (
        f'<testcase classname="tests.m" name="{name}" time="0.0">'
        '<failure message="assert 0">boom</failure></testcase>'
    )


def _errored(name: str) -> str:
    return (
        f'<testcase classname="tests.m" name="{name}" time="0.0">'
        '<error message="fixture failed">boom</error></testcase>'
    )


def _run(junit: Path, expect: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--junit", str(junit), "--expect", expect],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=60,
    )


def test_all_expected_passed_exits_0(tmp_path):
    junit = _junit(tmp_path, _passed("test_a") + _passed("test_b"))
    proc = _run(junit, "test_a,test_b")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: all 2 tests PASSED" in proc.stdout
    assert "FAIL" not in proc.stdout + proc.stderr


def test_parametrised_instances_all_passed_exits_0(tmp_path):
    junit = _junit(tmp_path, _passed("test_a[x]") + _passed("test_a[y]"))
    proc = _run(junit, "test_a")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_one_skipped_exits_1_and_names_it(tmp_path):
    junit = _junit(tmp_path, _passed("test_a") + _skipped("test_b"))
    proc = _run(junit, "test_a,test_b")
    assert proc.returncode == 1
    assert "skipped=test_b" in proc.stdout
    assert "FAIL" in proc.stderr


def test_all_skipped_exits_1(tmp_path):
    """The exact shape pytest produces when DMS_LEDGER_DSN is empty: exit 0, all skipped."""
    junit = _junit(tmp_path, _skipped("test_rls_blocks_out_of_scope_read"))
    proc = _run(junit, "test_rls_blocks_out_of_scope_read")
    assert proc.returncode == 1
    assert "skipped=test_rls_blocks_out_of_scope_read" in proc.stdout


def test_one_missing_exits_1_and_names_it(tmp_path):
    junit = _junit(tmp_path, _passed("test_a"))
    proc = _run(junit, "test_a,test_b")
    assert proc.returncode == 1
    assert "missing=test_b" in proc.stdout


def test_one_failed_exits_1_and_names_it(tmp_path):
    junit = _junit(tmp_path, _passed("test_a") + _failed("test_b"))
    proc = _run(junit, "test_a,test_b")
    assert proc.returncode == 1
    assert "failed=test_b" in proc.stdout


def test_one_error_exits_1_and_names_it(tmp_path):
    junit = _junit(tmp_path, _passed("test_a") + _errored("test_b"))
    proc = _run(junit, "test_a,test_b")
    assert proc.returncode == 1
    assert "errored=test_b" in proc.stdout


def test_empty_suite_exits_1(tmp_path):
    junit = _junit(tmp_path, "")
    proc = _run(junit, "test_a")
    assert proc.returncode == 1
    assert "empty suite" in proc.stdout
    assert "missing=test_a" in proc.stdout


def test_missing_junit_file_exits_1(tmp_path):
    proc = _run(tmp_path / "nope.xml", "test_a")
    assert proc.returncode == 1
    assert "junit file not found" in proc.stdout


def test_empty_expect_list_exits_1(tmp_path):
    junit = _junit(tmp_path, _passed("test_a"))
    proc = _run(junit, " , ")
    assert proc.returncode == 1


def test_unexpected_skip_in_same_file_exits_1(tmp_path):
    junit = _junit(tmp_path, _passed("test_a") + _skipped("test_new"))
    proc = _run(junit, "test_a")
    assert proc.returncode == 1
    assert "skipped=test_new" in proc.stdout


def test_real_rls_step_without_dsn_is_rejected(tmp_path):
    """End to end: the RLS deny step as CI runs it, but with the DSN empty.

    pytest itself exits 0 (every test skipped); the guard must turn that into 1.
    """
    junit = tmp_path / "rls-junit.xml"
    env = {**os.environ, "DMS_LEDGER_DSN": "", "PACK": "dms"}
    pytest_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/dms/test_rls_blocks_out_of_scope_read.py",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=300,
    )
    assert pytest_proc.returncode == 0, pytest_proc.stdout + pytest_proc.stderr
    proc = _run(junit, "test_rls_blocks_out_of_scope_read")
    assert proc.returncode == 1, proc.stdout
    assert "skipped=test_rls_blocks_out_of_scope_read" in proc.stdout


def test_real_green_pytest_run_is_accepted(tmp_path):
    """No false positive: a genuine pytest run where everything passes exits 0."""
    mod = tmp_path / "test_green_probe.py"
    mod.write_text("def test_one():\n    assert True\n\ndef test_two():\n    assert True\n")
    junit = tmp_path / "green.xml"
    pytest_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(mod),
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            f"--junitxml={junit}",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert pytest_proc.returncode == 0, pytest_proc.stdout + pytest_proc.stderr
    proc = _run(junit, "test_one,test_two")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: all 2 tests PASSED" in proc.stdout
