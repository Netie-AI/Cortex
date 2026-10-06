"""SCHEMA-RETRIEVE (#306) import contracts 5 and 6.

Contract 5: deterministic retrieval (``core``, ``catalog``) never reaches a
model, a pack, a DB driver or the answer plane. Contract 6: the whole package
reaches a model only through OV FreeRoute — never a provider SDK.

Each must-fail runs the real ``lint-imports`` on a scratch tree carrying the
contract copied verbatim from the repo's ``.importlinter``, and asserts the
offending import BREAKS it while the clean tree KEEPS it. grimp does not see
function-level imports, so the AST test covers those on the real modules.
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
TAG = "SCHEMA-RETRIEVE #306"
CORE_FORBIDDEN = (
    "packs",
    "duckdb",
    "litellm",
    "openai",
    "anthropic",
    "httpx",
    "CortexOS.integrations",
    "CortexOS.crew",
    "CortexOS.nlp",
    "CortexOS.execution",
    "CortexOS.dms",
    "CortexOS.api",
)
PACKAGE_FORBIDDEN = (
    "packs",
    "duckdb",
    "litellm",
    "openai",
    "anthropic",
    "groq",
    "httpx",
    "requests",
    "CortexOS.crew",
    "CortexOS.nlp",
    "CortexOS.execution",
    "CortexOS.dms",
    "CortexOS.api",
)


def _section(n: int) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    name = f"importlinter:contract:{n}"
    assert parser.has_section(name), f"{name} ({TAG}) missing from .importlinter"
    return parser[name]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_contracts_5_and_6_are_present_and_scoped() -> None:
    five, six = _section(5), _section(6)
    assert five["type"] == six["type"] == "forbidden"
    assert TAG in five["name"] and TAG in six["name"]
    assert _lines(five["source_modules"]) == [
        "CortexOS.schema_retrieve.core",
        "CortexOS.schema_retrieve.catalog",
    ]
    assert _lines(six["source_modules"]) == ["CortexOS.schema_retrieve"]
    assert set(_lines(five["forbidden_modules"])) >= set(CORE_FORBIDDEN)
    assert set(_lines(six["forbidden_modules"])) >= set(PACKAGE_FORBIDDEN)
    assert "CortexOS.integrations" not in _lines(six["forbidden_modules"])
    assert "ignore_imports" not in five and "ignore_imports" not in six


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


_PKGS = (
    "CortexOS",
    "CortexOS/schema_retrieve",
    "CortexOS/integrations",
    "CortexOS/memory",
    "CortexOS/crew",
    "CortexOS/nlp",
    "CortexOS/execution",
    "CortexOS/dms",
    "CortexOS/api",
    "packs",
    "packs/dms",
)


def _scratch_tree(tmp: Path, n: int, module: str, offending_import: str | None) -> Path:
    section = _section(n)
    for pkg in _PKGS:
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    for stub in ("integrations/freeroute.py", "memory/space_memory.py", "dms/answer_engine.py"):
        (tmp / "CortexOS" / stub).write_text("import json\n", encoding="utf-8")
    sources = {
        "core": "import re\nimport CortexOS.memory.space_memory\n",
        "catalog": "import yaml\nimport CortexOS.schema_retrieve.core\n",
        "freeroute_rerank": "import json\nimport CortexOS.integrations.freeroute\n",
    }
    for name, body in sources.items():
        if name == module and offending_import:
            body += f"import {offending_import}\n"
        (tmp / "CortexOS" / "schema_retrieve" / f"{name}.py").write_text(body, encoding="utf-8")
    contract_lines = [f"[importlinter:contract:{n}]"] + [
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
    return subprocess.run(
        [_lint_imports(), "--config", str(config), "--no-cache"],
        cwd=tmp,
        env={**os.environ, "PYTHONPATH": str(tmp)},
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


@pytest.mark.parametrize(
    "contract, module, offending_import",
    [
        (5, "core", "CortexOS.integrations.freeroute"),  # any model path
        (5, "core", "litellm"),
        (5, "catalog", "packs.dms"),  # a pack (C2)
        (5, "core", "duckdb"),  # a DB driver
        (5, "catalog", "CortexOS.dms.answer_engine"),  # the answer plane
        (6, "freeroute_rerank", "openai"),  # a provider SDK
        (6, "freeroute_rerank", "anthropic"),
        (6, "freeroute_rerank", "litellm"),
        (6, "freeroute_rerank", "httpx"),
        (6, "freeroute_rerank", "packs.dms"),
        (6, "freeroute_rerank", "CortexOS.dms.answer_engine"),
    ],
)
def test_must_fail_import_contract_breaks(
    tmp_path: Path, contract: int, module: str, offending_import: str
) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, contract, module, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert TAG in out.replace("\n", " "), out
    assert offending_import.split(".")[0] in out, out


@pytest.mark.parametrize("contract", [5, 6])
def test_import_contract_keeps_on_clean_tree(tmp_path: Path, contract: int) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, contract, "", None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "KEPT" in out and "1 kept, 0 broken" in out, out


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _bad(names: list[str], forbidden: tuple[str, ...]) -> list[str]:
    return [n for n in names if any(n == f or n.startswith(f + ".") for f in forbidden)]


def test_no_forbidden_import_at_any_level() -> None:
    pkg = ROOT / "CortexOS" / "schema_retrieve"
    for name in ("core.py", "catalog.py"):
        assert _bad(_imports(pkg / name), CORE_FORBIDDEN) == [], name
    for path in pkg.glob("*.py"):
        names = _imports(path)
        assert _bad(names, PACKAGE_FORBIDDEN) == [], path.name
        assert "netie" not in {n.split(".")[0] for n in names}, path.name
