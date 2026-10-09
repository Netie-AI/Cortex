"""SUGGEST (#308) import contract: follow-ups reach a model only through FreeRoute.

``test_must_fail_import_contract_breaks`` runs the real ``lint-imports`` against
a scratch tree carrying contract 5 copied verbatim from the repo's
``.importlinter``: each provider SDK, pack, DB driver or answer-plane import
must BREAK it, and the clean tree (which does import FreeRoute) must keep it.
The AST test checks the real package at every nesting level, including
string-based imports grimp cannot see.
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
SECTION = "importlinter:contract:5"
SOURCE = "CortexOS.suggest"
PROVIDERS = ("anthropic", "openai", "litellm", "httpx", "requests", "aiohttp", "google", "cohere", "mistralai", "groq")
FORBIDDEN = PROVIDERS + (
    "packs",
    "duckdb",
    "CortexOS.nlp",
    "CortexOS.crew",
    "CortexOS.dms",
    "CortexOS.api",
    "CortexOS.execution",
)
IGNORED = ["CortexOS.ontology.registry -> packs.dms.audit.ledger"]
MODEL_PATH = "CortexOS.integrations.freeroute"
MODEL_MODULE = "freeroute_rephrase.py"


def _contract() -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(SECTION), f"{SECTION} (SUGGEST) missing from .importlinter"
    return dict(parser[SECTION])


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_import_contract_5_is_present_and_scoped() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "SUGGEST #308" in section["name"]
    assert _lines(section["source_modules"]) == [SOURCE]
    assert set(_lines(section["forbidden_modules"])) >= set(FORBIDDEN)
    assert _lines(section.get("ignore_imports", "")) == IGNORED


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _write(tmp: Path, rel: str, body: str = "") -> None:
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _scratch_tree(tmp: Path, offending_import: str | None, *, into: str = "followups") -> Path:
    section = _contract()
    for pkg in (
        "CortexOS", "CortexOS/suggest", "CortexOS/integrations", "CortexOS/ontology",
        "CortexOS/nlp", "CortexOS/crew", "CortexOS/execution", "CortexOS/dms", "CortexOS/api",
        "packs", "packs/dms", "packs/dms/audit",
    ):
        _write(tmp, f"{pkg}/__init__.py")
    _write(tmp, "CortexOS/integrations/openvault_client.py", "import urllib.request\n")
    _write(tmp, "CortexOS/integrations/freeroute.py", "from CortexOS.integrations import openvault_client\n")
    _write(tmp, "CortexOS/ontology/registry.py", "def compile_to_sqlite():\n    from packs.dms.audit.ledger import x\n")
    _write(tmp, "CortexOS/dms/answer_engine.py")
    _write(tmp, "packs/dms/audit/ledger.py", "x = 1\n")
    modules = {
        "followups": "import re\n",
        "ask": "import sqlglot\nfrom CortexOS.ontology.registry import compile_to_sqlite\n",
        "freeroute_rephrase": f"from {MODEL_PATH} import *\n",
    }
    if offending_import:
        modules[into] += f"import {offending_import}\n"
    for name, body in modules.items():
        _write(tmp, f"CortexOS/suggest/{name}.py", body)
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
        cwd=tmp, env=env, text=True, capture_output=True, check=False, timeout=120,
    )


@pytest.mark.parametrize(
    ("offending_import", "into"),
    [(p, "followups") for p in PROVIDERS]
    + [
        ("packs.dms", "followups"),
        ("packs.dms.audit.ledger", "ask"),  # the ignore covers the registry edge only
        ("duckdb", "ask"),
        ("CortexOS.dms.answer_engine", "ask"),
        ("CortexOS.execution", "freeroute_rephrase"),
    ],
)
def test_must_fail_import_contract_breaks(tmp_path: Path, offending_import: str, into: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import, into=into))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "SUGGEST #308" in " ".join(out.split()), out
    assert offending_import.split(".")[0] in out, out


def test_import_contract_keeps_on_clean_tree_that_uses_freeroute(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "KEPT" in out and "1 kept, 0 broken" in out, out


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.extend(
                [node.module]
                + [f"{node.module}.{a.name}" for a in node.names if node.module == "CortexOS.integrations"]
            )
        elif isinstance(node, ast.Call):
            fn = node.func
            label = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            assert label not in {"import_module", "__import__"}, f"{path.name}: dynamic import"
    return names


def test_suggest_package_has_no_forbidden_import_at_any_level() -> None:
    files = sorted((ROOT / "CortexOS" / "suggest").glob("*.py"))
    assert {f.name for f in files} >= {"ask.py", "followups.py", MODEL_MODULE}
    for path in files:
        names = _imports(path)
        bad = [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
        assert bad == [], f"{path.name} imports {bad}"
        integrations = {n for n in names if n.startswith("CortexOS.integrations")}
        if path.name == MODEL_MODULE:
            assert integrations == {"CortexOS.integrations", MODEL_PATH}, integrations
        else:
            assert integrations == set(), f"{path.name} reaches {integrations}"
