"""Import contract 2: the packaging helpers never import the optional planes.

Contract 2 used ``type = independence`` with an ``independent_from`` option that
independence does not have. import-linter up to 2.15 ignored the option, so the
contract checked ``CortexOS.packaging`` against nothing and could not fail; 2.16+
refuses the file. It is now ``type = forbidden`` with the same modules.

``test_must_fail_import_contract_breaks`` runs the real ``lint-imports`` against a
scratch tree carrying contract 2 copied from the repo's ``.importlinter``: each
forbidden import must BREAK it and the clean tree must keep it. The AST test
covers function-level imports, which grimp does not see.
"""

from __future__ import annotations

import ast
import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SECTION = "importlinter:contract:2"
SOURCE = "CortexOS.packaging"
FORBIDDEN = (
    "CortexOS.rag",
    "CortexOS.execution.dag_runner",
    "CortexOS.execution.osr",
    "CortexOS.execution.seeker",
)


def _contract() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(SECTION), f"{SECTION} missing from .importlinter"
    return parser[SECTION]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_import_contract_2_is_present_and_scoped() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "independent_from" not in section
    assert _lines(section["source_modules"]) == [SOURCE]
    assert set(_lines(section["forbidden_modules"])) == set(FORBIDDEN)
    assert "ignore_imports" not in section


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _scratch_tree(tmp: Path, offending_import: str | None) -> Path:
    section = _contract()
    for pkg in ("CortexOS", "CortexOS/rag", "CortexOS/execution"):
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    for mod in ("dag_runner", "osr", "seeker"):
        (tmp / f"CortexOS/execution/{mod}.py").write_text("", encoding="utf-8")
    body = "import os\n" + (f"import {offending_import}\n" if offending_import else "")
    (tmp / "CortexOS/packaging.py").write_text(body, encoding="utf-8")
    contract_lines = [f"[{SECTION}]"] + [
        f"{key} =" + ("\n    " + "\n    ".join(_lines(value)) if "\n" in value else f" {value}")
        for key, value in section.items()
    ]
    config = tmp / ".importlinter"
    config.write_text(
        "[importlinter]\nroot_packages =\n    CortexOS\n\n" + "\n".join(contract_lines) + "\n",
        encoding="utf-8",
    )
    return config


def _run(tmp: Path, config: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(tmp)}
    return subprocess.run(
        [_lint_imports(), "--config", str(config), "--no-cache"],
        cwd=tmp,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


@pytest.mark.parametrize("offending_import", FORBIDDEN)
def test_must_fail_import_contract_breaks(tmp_path: Path, offending_import: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "packaging helpers stay independent" in out.replace("\n", " "), out


def test_import_contract_keeps_on_clean_tree(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 kept, 0 broken" in out, out


def test_packaging_has_no_forbidden_import_at_any_level() -> None:
    tree = ast.parse((ROOT / "CortexOS" / "packaging.py").read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    bad = [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
    assert bad == [], f"CortexOS/packaging.py imports {bad}"
