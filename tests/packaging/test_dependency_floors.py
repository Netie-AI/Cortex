"""Floors in [project].dependencies must be the ones pip resolves.

A second table under [tool.poetry.dependencies] used caret caps
(cryptography ^42 => >=42,<43) that could not reach patched releases even
when [project] was loosened. The build-backend is poetry-core but every
install path is pip install -e ".[...]".
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"

# CVE floors established 2026-08-27 (Dependabot: litellm, cryptography, pillow,
# starlette). fastapi must move with starlette or the resolver cannot reach 1.3.1.
_FLOORS = {
    "litellm": "1.84.0",
    "cryptography": "50.0.0",
    "pillow": "12.3.0",
    "starlette": "1.3.1",
    "fastapi": "0.141",
}


def _project() -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_no_second_dependency_table() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "[tool.poetry.dependencies]" not in text, (
        "caret table is dead metadata and silently caps patched versions; "
        "pip resolves [project].dependencies only"
    )


def test_vulnerable_floors_are_high_enough() -> None:
    data = _project()
    deps = list(data["project"]["dependencies"])
    blob = " ".join(deps).lower()
    for name, floor in _FLOORS.items():
        assert name in blob, f"{name} missing from [project].dependencies"
        token = f"{name}>={floor}"
        assert any(token in d.replace(" ", "").lower() for d in deps), (
            f"{name} must be >={floor} (found {deps})"
        )


def test_starlette_is_a_direct_dependency() -> None:
    """starlette is imported by CortexOS but was only transitive via fastapi."""
    data = _project()
    names = [d.split(">")[0].split("=")[0].split("[")[0].strip().lower()
             for d in data["project"]["dependencies"]]
    assert "starlette" in names
