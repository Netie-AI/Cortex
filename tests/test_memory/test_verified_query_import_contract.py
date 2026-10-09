"""VERIFIED-QUERY (#309) import contract: the library never reaches a model,
a pack, a DB driver or the answer plane.

``test_must_fail_import_contract_breaks`` runs the real ``lint-imports`` against
a scratch tree carrying the named contract copied from ``.importlinter``. A
planted forbidden import must BREAK it, and the clean tree must keep it.
The id is ``importlinter:contract:verified-query-library``, not a number.
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
SECTION = "importlinter:contract:verified-query-library"
SOURCE = "CortexOS.memory.verified_query"
FORBIDDEN = (
    "packs",
    "duckdb",
    "litellm",
    "httpx",
    "CortexOS.integrations",
    "CortexOS.crew",
    "CortexOS.nlp",
    "CortexOS.execution",
    "CortexOS.dms",
    "CortexOS.api",
)


def _contract() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(SECTION), f"{SECTION} missing from .importlinter"
    return parser[SECTION]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_import_contract_verified_query_is_named_and_scoped() -> None:
    section = _contract()
    assert section.name == SECTION
    assert section["type"] == "forbidden"
    assert "VERIFIED-QUERY #309" in section["name"]
    assert _lines(section["source_modules"]) == [SOURCE]
    assert set(_lines(section["forbidden_modules"])) >= set(FORBIDDEN)
    assert "ignore_imports" not in section
    assert not SECTION.rsplit(":", 1)[-1].isdigit()


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _scratch_tree(tmp: Path, offending_import: str | None) -> Path:
    section = _contract()
    for pkg in (
        "CortexOS",
        "CortexOS/memory",
        "CortexOS/integrations",
        "CortexOS/crew",
        "CortexOS/nlp",
        "CortexOS/execution",
        "CortexOS/dms",
        "CortexOS/api",
        "packs",
        "packs/dms",
    ):
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/integrations/freeroute.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/dms/answer_engine.py").write_text("", encoding="utf-8")
    body = "import json\n" + (f"import {offending_import}\n" if offending_import else "")
    (tmp / "CortexOS/memory/verified_query.py").write_text(body, encoding="utf-8")
    contract_lines = [f"[{SECTION}]"] + [
        f"{key} =" + ("\n    " + "\n    ".join(_lines(value)) if "\n" in value else f" {value}")
        for key, value in section.items()
    ]
    config = tmp / ".importlinter"
    config.write_text(
        "[importlinter]\nroot_packages =\n    CortexOS\n    packs\n"
        "include_external_packages = True\n\n" + "\n".join(contract_lines) + "\n",
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


@pytest.mark.parametrize(
    "offending_import",
    [
        "CortexOS.integrations.freeroute",
        "litellm",
        "packs.dms",
        "duckdb",
        "CortexOS.dms.answer_engine",
    ],
)
def test_must_fail_import_contract_breaks(tmp_path: Path, offending_import: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "VERIFIED-QUERY #309" in out.replace("\n", " "), out
    assert offending_import.split(".")[0] in out, out


def test_import_contract_keeps_on_clean_tree(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "KEPT" in out and "1 kept, 0 broken" in out, out


def test_verified_query_module_has_no_forbidden_import_at_any_level() -> None:
    path = ROOT / "CortexOS" / "memory" / "verified_query.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    bad = [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
    assert bad == [], f"verified_query imports {bad}"
