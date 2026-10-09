"""CLARIFY (#307) import boundary: detection never reaches a model, a pack, a
DB driver, memory or the answer plane.

The contract lives here, not in ``.importlinter``: this slice adds no import
contract and edits no protected path. ``lint-imports`` runs for real with a
scratch config holding only this contract, once over the real tree (must KEEP)
and once per forbidden import over a scratch tree (must BREAK) - a gate that
cannot fail is not a gate. grimp does not see function-level imports, so the
AST tests cover those on the real modules, the ask seam included.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = "clarify detection never reaches a model, a pack, a DB driver or the answer plane (CLARIFY #307)"
SOURCE = "CortexOS.clarify"
FORBIDDEN = (
    "packs",
    "duckdb",
    "litellm",
    "httpx",
    "openai",
    "anthropic",
    "CortexOS.integrations",
    "CortexOS.crew",
    "CortexOS.nlp",
    "CortexOS.execution",
    "CortexOS.dms",
    "CortexOS.api",
    "CortexOS.memory",
)
# The seam reaches the answer engine's router by design; it still never
# imports a pack, a model client or the model integrations itself.
SEAM_FORBIDDEN = ("packs", "litellm", "httpx", "openai", "anthropic", "CortexOS.integrations")


def _config(path: Path) -> Path:
    forbidden = "\n    ".join(FORBIDDEN)
    path.write_text(
        "[importlinter]\nroot_packages =\n    CortexOS\n    packs\n"
        "include_external_packages = True\n\n"
        "[importlinter:contract:clarify]\n"
        f"name = {NAME}\n"
        "type = forbidden\n"
        f"source_modules =\n    {SOURCE}\n"
        f"forbidden_modules =\n    {forbidden}\n",
        encoding="utf-8",
    )
    return path


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _run(cwd: Path, config: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(cwd)}
    return subprocess.run(
        [_lint_imports(), "--config", str(config), "--no-cache"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )


def _scratch_tree(tmp: Path, offending_import: str | None) -> Path:
    for pkg in (
        "CortexOS",
        "CortexOS/clarify",
        "CortexOS/integrations",
        "CortexOS/crew",
        "CortexOS/nlp",
        "CortexOS/execution",
        "CortexOS/dms",
        "CortexOS/api",
        "CortexOS/memory",
        "packs",
        "packs/dms",
    ):
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/integrations/freeroute.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/dms/answer_engine.py").write_text("", encoding="utf-8")
    body = "import json\n" + (f"import {offending_import}\n" if offending_import else "")
    (tmp / "CortexOS/clarify/core.py").write_text(body, encoding="utf-8")
    return _config(tmp / ".importlinter")


@pytest.mark.parametrize(
    "offending_import",
    [
        "CortexOS.integrations.freeroute",  # the model path
        "litellm",  # a model client
        "packs.dms",  # a pack (C2)
        "duckdb",  # a DB driver
        "CortexOS.dms.answer_engine",  # the answer plane
    ],
)
def test_must_fail_import_contract_breaks(tmp_path: Path, offending_import: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "CLARIFY #307" in out.replace("\n", " "), out
    assert offending_import.split(".")[0] in out, out


def test_import_contract_keeps_on_clean_scratch_tree(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 kept, 0 broken" in out, out


def test_import_contract_keeps_on_the_real_tree(tmp_path: Path) -> None:
    proc = _run(ROOT, _config(tmp_path / "clarify.importlinter"))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 kept, 0 broken" in out, out


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _bad(names: list[str], forbidden: tuple[str, ...]) -> list[str]:
    return [n for n in names if any(n == f or n.startswith(f + ".") for f in forbidden)]


@pytest.mark.parametrize("path", sorted((ROOT / "CortexOS" / "clarify").glob("*.py")), ids=str)
def test_clarify_core_has_no_forbidden_import_at_any_level(path: Path) -> None:
    names = _imports(path)
    assert _bad(names, FORBIDDEN) == [], f"{path.name} imports {_bad(names, FORBIDDEN)}"
    assert "netie" not in {n.split(".")[0] for n in names}


def test_ask_seam_imports_no_pack_and_no_model_path() -> None:
    names = _imports(ROOT / "CortexOS" / "dms" / "clarify_ask.py")
    assert _bad(names, SEAM_FORBIDDEN) == [], _bad(names, SEAM_FORBIDDEN)
    assert "netie" not in {n.split(".")[0] for n in names}
