"""H2-TEST-ISOLATION (#254): tests never write the runtime decision or shadow logs.

Loaded for the whole suite as a pytest plugin through
``[tool.pytest.ini_options] addopts = "-p tests.runtime_log_isolation"`` in
``pyproject.toml``. It lives here rather than in ``tests/conftest.py`` because
that file belongs to the #215 FreeRoute layer and
``tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer``
forbids any branch diff to it.

``CortexOS.decision.decision_log`` and ``CortexOS.decision.shadow`` append to
``data/engine/tier_decisions.jsonl`` and ``data/engine/tier_shadow.jsonl`` by
default. KEV-CALIB reads those files as real outcomes, so a test that runs a
routed DAG without redirecting them poisons the calibration corpus with
synthetic rows (46 per full run on a clean checkout, verified on 275fe13).

The env names are spelled here rather than imported so that the plugin does not
import the engine before collection; the meta test
``tests/test_meta/test_runtime_logs_untouched.py`` asserts they match
``decision_log.PATH_ENV`` and ``shadow.PATH_ENV``.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

RUNTIME_LOG_ENV = {
    "CORTEX_DECISION_LOG_PATH": "tier_decisions.jsonl",
    "CORTEX_KEV_SHADOW_PATH": "tier_shadow.jsonl",
}
RUNTIME_LOG_GUARD_ENV = "CORTEX_RUNTIME_LOG_GUARD"
_RUNTIME_LOG_SNAPSHOT: pytest.StashKey[dict[str, tuple[int, int] | None]] = pytest.StashKey()


def _runtime_log_files() -> dict[str, Path]:
    """The real runtime files, resolved the way the engine resolves them (repo
    anchored, never cwd) but without the env override, so the snapshot is of the
    files the fixture is meant to protect even when a shell exports an override."""
    root = Path(__file__).resolve().parents[1]
    return {name: root / "data" / "engine" / name for name in RUNTIME_LOG_ENV.values()}


def snapshot_runtime_logs() -> dict[str, tuple[int, int] | None]:
    """``{filename: (size, mtime_ns)}``, or ``None`` when the file is absent."""
    out: dict[str, tuple[int, int] | None] = {}
    for name, path in _runtime_log_files().items():
        try:
            st = path.stat()
        except FileNotFoundError:
            out[name] = None
        else:
            out[name] = (st.st_size, st.st_mtime_ns)
    return out


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_RUNTIME_LOG_SNAPSHOT] = snapshot_runtime_logs()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """CI guard: the suite must leave the runtime logs byte-identical.

    Always reports a change in the terminal. Fails the session (exit 1) only when
    ``CORTEX_RUNTIME_LOG_GUARD=1``: on a laptop a live engine may legitimately
    append to these files while the suite runs, and a guard that fails a green
    run because of unrelated production traffic is itself a defect.
    """
    before = session.config.stash.get(_RUNTIME_LOG_SNAPSHOT, None)
    if before is None:
        return
    after = snapshot_runtime_logs()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    if not changed:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    lines = [f"runtime log {k} changed during the test session: {b} -> {a}" for k, (b, a) in changed.items()]
    if reporter is not None:
        reporter.ensure_newline()
        reporter.section("H2-TEST-ISOLATION", sep="=", red=True, bold=True)
        for line in lines:
            reporter.line(line, red=True)
    else:
        for line in lines:
            sys.stderr.write(line + "\n")
    if os.environ.get(RUNTIME_LOG_GUARD_ENV, "").strip() == "1":
        session.exitstatus = max(int(exitstatus), 1)


@pytest.fixture(scope="session")
def runtime_log_guard(request: pytest.FixtureRequest) -> types.SimpleNamespace:
    """Handle on the guard for meta tests.

    ``baseline``: size and mtime of both runtime files (or None) at session start;
    ``snapshot()``: the same reading now; ``files``: the real paths; ``env``: the
    env name to filename map the autouse fixture sets.
    """
    return types.SimpleNamespace(
        baseline=dict(request.config.stash[_RUNTIME_LOG_SNAPSHOT]),
        snapshot=snapshot_runtime_logs,
        files=_runtime_log_files(),
        env=dict(RUNTIME_LOG_ENV),
        guard_env=RUNTIME_LOG_GUARD_ENV,
    )


@pytest.fixture(autouse=True)
def runtime_logs_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Point the decision log and the shadow log at a per-test tmp path.

    Autouse fixtures run before a test's non-autouse fixtures and its body, so a
    test that sets ``CORTEX_DECISION_LOG_PATH`` / ``CORTEX_KEV_SHADOW_PATH``
    itself (via ``monkeypatch.setenv`` in a fixture or the body) still wins; this
    only replaces the default, which would otherwise be the real ``data/engine/``
    files.
    """
    paths: dict[str, Path] = {}
    for env_name, filename in RUNTIME_LOG_ENV.items():
        path = tmp_path / "runtime_logs" / filename
        monkeypatch.setenv(env_name, str(path))
        paths[env_name] = path
    return paths
