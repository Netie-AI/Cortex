"""Optional DBOS Transact runtime for S1 durable agent resume.

Install: ``pip install -e ".[agents]"`` (pins ``dbos>=3.0.0,<4``).
Without the library, callers fall back to ops-DB step checkpoints in
``registry`` — same resume semantics, no external orchestrator.

Temporal is intentionally not used (BUILD_PLAN / S1_DBOS_RESUME anti-scope).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from dbos import DBOS, DBOSConfig, SetWorkflowID

    HAS_DBOS = True
except ImportError:  # pragma: no cover - exercised when [agents] extra missing
    DBOS = None  # type: ignore[misc, assignment]
    DBOSConfig = dict  # type: ignore[misc, assignment]
    SetWorkflowID = None  # type: ignore[misc, assignment]
    HAS_DBOS = False

_configured = False
_launched = False
_generation = 0
_configured_url: str | None = None
APP_NAME = "cortex-dms-agents"
# Keep stable across dbos upgrades: launch() only recovers PENDING workflows of the
# current version, and run_agent() re-entering an unrecovered workflow_id blocks
# forever on its result. Stop every dbos 2.x process before starting a 3.x one
# instead (3.x reads 2.x workflows; 2.x cannot read 3.x ones).
APP_VERSION = "s1-dbos-resume-v1"


def generation() -> int:
    """Bumps on destroy(); callers re-bind step wrappers after a simulated kill."""
    return _generation


def _system_database_url() -> str | None:
    env = (os.environ.get("DBOS_SYSTEM_DATABASE_URL") or "").strip()
    if env:
        # Prefer forward-slash paths on Windows (sqlite:///C:/...).
        if env.lower().startswith("sqlite:") and "\\" in env:
            return env.replace("\\", "/")
        return env
    return None


def ensure_configured(*, system_database_url: str | None = None) -> bool:
    """Construct the DBOS singleton once. Returns False if dbos is not installed."""
    global _configured, _configured_url
    if not HAS_DBOS:
        return False
    url = system_database_url if system_database_url is not None else _system_database_url()
    if not url:
        data = Path("data")
        data.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{(data / 'dbos_agents.sqlite').resolve().as_posix()}"
    if _configured:
        if _configured_url == url:
            return True
        destroy()
    config: dict[str, Any] = {
        "name": APP_NAME,
        "application_version": APP_VERSION,
        "system_database_url": url,
    }
    DBOS(config=config)  # type: ignore[misc]
    _configured = True
    _configured_url = url
    return True


def ensure_launched(**kwargs: Any) -> bool:
    """Configure + launch DBOS (recovers incomplete workflows). No-op without dbos."""
    global _launched
    if not ensure_configured(**kwargs):
        return False
    if _launched:
        return True
    DBOS.launch()  # type: ignore[union-attr]
    _launched = True
    return True


def destroy() -> None:
    """Tear down DBOS (tests / simulated process kill). Safe if never launched."""
    global _configured, _launched, _generation, _configured_url
    if HAS_DBOS and _configured:
        try:
            DBOS.destroy()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    _configured = False
    _launched = False
    _configured_url = None
    _generation += 1


def set_workflow_id(workflow_id: str):
    """Context manager; no-op identity context when dbos is absent."""
    if HAS_DBOS and SetWorkflowID is not None:
        return SetWorkflowID(workflow_id)

    from contextlib import nullcontext

    return nullcontext()
