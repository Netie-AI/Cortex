"""Auth assertions fail closed when CORTEX_DEV_MODE is set.

The guard lives in tests/conftest.py (AUTH_TEST_IN_DEV_MODE). This runs one
constructor auth test under that switch and requires the named failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_AUTH_TEST = (
    "tests/packaging/test_constructor_hyperlift_dockerfile.py"
    "::test_constructor_image_keeps_auth_on_and_seeds_warehouse"
)


def test_guard_matches_spend_auth_flag_table_and_constructor_auth() -> None:
    """Path and name coverage for the conftest guard. No dev-mode process."""
    from image_auth_guard import _is_auth_assertion

    class _Item:
        def __init__(self, nodeid: str, name: str) -> None:
            self.nodeid = nodeid
            self.name = name

    spend = "tests/test_api/test_run_spend_auth.py::test_auth_disabled_does_not_widen_spend"
    assert _is_auth_assertion(_Item(spend, "test_auth_disabled_does_not_widen_spend"))
    assert _is_auth_assertion(
        _Item(
            "tests/foo/test_run_spend_auth_extra.py::test_star_allowlist_is_rejected_at_load",
            "test_star_allowlist_is_rejected_at_load",
        )
    )
    assert _is_auth_assertion(
        _Item(
            "tests/dms/test_constructor_graph.py::test_run_401_without_key",
            "test_run_401_without_key",
        )
    )
    assert _is_auth_assertion(
        _Item(
            "tests/dms/test_constructor_graph.py::test_session_sets_cookie_for_viewer_key",
            "test_session_sets_cookie_for_viewer_key",
        )
    )
    assert not _is_auth_assertion(
        _Item(
            "tests/dms/test_constructor_graph.py::test_compile_sample_has_exactly_one_emit",
            "test_compile_sample_has_exactly_one_emit",
        )
    )


def test_constructor_auth_test_is_red_when_dev_mode_set() -> None:
    env = os.environ.copy()
    env["CORTEX_DEV_MODE"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            _AUTH_TEST,
            "--tb=line",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "AUTH_TEST_IN_DEV_MODE" in combined, combined
