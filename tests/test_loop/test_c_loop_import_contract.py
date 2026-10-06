"""C-LOOP (#291) import contract 5: the analysis loop never routes a model.

``test_must_fail_import_contract_5_breaks`` runs the real ``lint-imports``
against a scratch tree carrying contract 5 copied verbatim from the repo's
``.importlinter``: each forbidden import must BREAK it, and the clean tree must
keep it. grimp does not see function-level imports, so the AST tests cover
those on the real package, and also refuse any provider or model literal in it.
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
SOURCE = "CortexOS.loop"
LOOP_DIR = ROOT / "CortexOS" / "loop"
FORBIDDEN = (
    "packs",
    "duckdb",
    "litellm",
    "httpx",
    "requests",
    "openai",
    "anthropic",
    "groq",
    "CortexOS.integrations",
    "CortexOS.routing",
    "CortexOS.nlp",
    "CortexOS.crew",
    "CortexOS.execution",
    "CortexOS.dms",
    "CortexOS.api",
)
# Not in contract 5 (stdlib is outside the import graph); enforced here instead.
FORBIDDEN_STDLIB = ("socket", "ssl", "urllib", "http", "subprocess", "sqlite3", "ftplib", "smtplib")
_PROVIDER_LITERALS = ("groq", "openai", "anthropic", "gemini", "mistral", "ollama", "gpt-", "claude", "llama")


def _contract() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(SECTION), f"{SECTION} (C-LOOP) missing from .importlinter"
    return parser[SECTION]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_import_contract_5_is_present_and_scoped() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "C-LOOP #291" in section["name"]
    assert _lines(section["source_modules"]) == [SOURCE]
    assert set(_lines(section["forbidden_modules"])) >= set(FORBIDDEN)
    assert "ignore_imports" not in section
    assert section.get("allow_indirect_imports", "False") == "False"


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _scratch_tree(tmp: Path, offending_import: str | None) -> Path:
    section = _contract()
    for pkg in (
        "CortexOS",
        "CortexOS/loop",
        "CortexOS/memory",
        "CortexOS/integrations",
        "CortexOS/routing",
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
    (tmp / "CortexOS/routing/judgment_model.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/dms/answer_engine.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/memory/space_memory.py").write_text("import json\n", encoding="utf-8")
    body = "import CortexOS.memory.space_memory\n" + (
        f"import {offending_import}\n" if offending_import else ""
    )
    (tmp / "CortexOS/loop/runner.py").write_text(body, encoding="utf-8")
    config = tmp / ".importlinter"
    contract_lines = [f"[{SECTION}]"] + [
        f"{key} =" + ("\n    " + "\n    ".join(_lines(value)) if "\n" in value else f" {value}")
        for key, value in section.items()
    ]
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
        "CortexOS.integrations.freeroute",  # the model path: only the injected engine may use it
        "CortexOS.routing.judgment_model",  # a model router
        "litellm",  # a model client
        "packs.dms",  # a pack (C2)
        "duckdb",  # a DB driver
        "CortexOS.dms.answer_engine",  # the answer plane
    ],
)
def test_must_fail_import_contract_5_breaks(tmp_path: Path, offending_import: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "C-LOOP #291" in out.replace("\n", " "), out
    assert offending_import.split(".")[0] in out, out


def test_import_contract_5_keeps_on_clean_tree(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "KEPT" in out and "1 kept, 0 broken" in out, out


def _loop_sources() -> list[Path]:
    files = sorted(LOOP_DIR.glob("*.py"))
    assert {p.name for p in files} >= {"__init__.py", "runner.py", "tools.py", "sandbox.py"}
    return files


def test_loop_has_no_forbidden_import_at_any_level() -> None:
    bad: list[str] = []
    for path in _loop_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in (*FORBIDDEN, *FORBIDDEN_STDLIB)):
                    bad.append(f"{path.name}: {name}")
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
                bad.append(f"{path.name}: dynamic __import__")
    assert bad == [], bad


def test_loop_never_names_a_provider_or_model() -> None:
    hits: list[str] = []
    for path in _loop_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                low = node.value.lower()
                hits.extend(f"{path.name}: {node.value!r}" for p in _PROVIDER_LITERALS if p in low)
            if isinstance(node, ast.keyword) and node.arg in {"model", "provider", "served_model"}:
                hits.append(f"{path.name}: keyword {node.arg}=")
    assert hits == [], hits
