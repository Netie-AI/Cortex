"""C-LOOP-A (#303) import contract ``plan-sql-freeroute-only``: the plan+SQL
path reaches a model only through OpenVault FreeRoute.

``test_must_fail_import_contract_breaks`` runs the real ``lint-imports`` against
a scratch tree carrying the contract copied verbatim from the repo's
``.importlinter``: each forbidden import must BREAK it and the clean tree (which
imports FreeRoute) must keep it, so the gate can fail. The AST tests are a
second net on the real files, including the ask seam outside the contract.
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
SECTION = "importlinter:contract:plan-sql-freeroute-only"
SOURCE = "CortexOS.plan_sql"
PROVIDER_SDKS = (
    "openai",
    "anthropic",
    "groq",
    "litellm",
    "cohere",
    "mistralai",
    "together",
    "ollama",
    "cerebras",
    "google",
    "vertexai",
    "boto3",
    "botocore",
    "huggingface_hub",
    "transformers",
    "httpx",
    "requests",
    "aiohttp",
    "urllib3",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langgraph",
    "llama_index",
)
OTHER_MODEL_PATHS = ("CortexOS.routing", "CortexOS.crew", "CortexOS.nlp", "CortexOS.fabrication")
FORBIDDEN = (
    *PROVIDER_SDKS,
    *OTHER_MODEL_PATHS,
    "packs",
    "duckdb",
    "CortexOS.execution",
    "CortexOS.dms",
    "CortexOS.api",
)
PACKAGE = ROOT / "CortexOS" / "plan_sql"
SEAM = ROOT / "CortexOS" / "dms" / "plan_sql_ask.py"


def _contract() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(SECTION), f"{SECTION} (C-LOOP-A) missing from .importlinter"
    return parser[SECTION]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_import_contract_is_present_named_and_scoped() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "C-LOOP-A #303" in section["name"]
    assert _lines(section["source_modules"]) == [SOURCE]
    assert set(_lines(section["forbidden_modules"])) >= set(FORBIDDEN)
    assert "CortexOS.integrations" not in _lines(section["forbidden_modules"])
    assert "ignore_imports" not in section
    assert "allow_indirect_imports" not in section


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _scratch_tree(tmp: Path, offending_import: str | None) -> Path:
    section = _contract()
    packages = [
        "CortexOS",
        "CortexOS/plan_sql",
        "CortexOS/integrations",
        *(m.replace(".", "/") for m in FORBIDDEN if m.startswith("CortexOS.")),
        "packs",
        "packs/dms",
    ]
    if offending_import and not offending_import.startswith(("CortexOS", "packs")):
        packages.append(offending_import.split(".")[0])
    for pkg in packages:
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/integrations/freeroute.py").write_text("import json\n", encoding="utf-8")
    (tmp / "CortexOS/execution/submit.py").write_text("", encoding="utf-8")
    body = "from CortexOS.integrations import freeroute\n" + (
        f"import {offending_import}\n" if offending_import else ""
    )
    (tmp / "CortexOS/plan_sql/generator.py").write_text(body, encoding="utf-8")
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
        "openai",  # a provider SDK
        "anthropic",  # a provider SDK
        "groq",  # a provider SDK
        "litellm",  # a provider router
        "httpx",  # a raw HTTP client
        "CortexOS.routing",  # another Cortex model router
        "CortexOS.crew",  # Crew's own model client
        "packs.dms",  # a pack (C2)
        "duckdb",  # a DB driver
        "CortexOS.execution.submit",  # the executor
    ],
)
def test_must_fail_import_contract_breaks(tmp_path: Path, offending_import: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "C-LOOP-A" in out.replace("\n", " "), out
    assert offending_import.split(".")[0] in out, out


def test_import_contract_keeps_on_clean_tree(tmp_path: Path) -> None:
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
            names.append(node.module)
    return names


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _hits(names: list[str], banned: tuple[str, ...]) -> list[str]:
    return [n for n in names if any(n == b or n.startswith(b + ".") for b in banned)]


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_plan_sql_package_has_no_model_path_but_freeroute(path: Path) -> None:
    names = _imports(path)
    assert _hits(names, FORBIDDEN) == [], f"{path.name} imports {_hits(names, FORBIDDEN)}"
    network = ("socket", "ssl", "http", "urllib", "importlib")
    assert _hits(names, network) == [], f"{path.name} imports {_hits(names, network)}"
    assert {"__import__", "exec", "eval"} & _calls(path) == set()
    assert "netie" not in {n.split(".")[0] for n in names}


def test_ask_seam_has_no_provider_sdk_or_other_model_router() -> None:
    names = _imports(SEAM)
    banned = (*PROVIDER_SDKS, *OTHER_MODEL_PATHS, "packs", "duckdb", "socket", "urllib")
    assert _hits(names, banned) == [], f"plan_sql_ask imports {_hits(names, banned)}"
    assert {"__import__", "exec", "eval"} & _calls(SEAM) == set()
    model_modules = [n for n in names if n.startswith("CortexOS.integrations")]
    assert model_modules == ["CortexOS.integrations"], model_modules


def test_generator_sends_every_model_call_through_freeroute_complete() -> None:
    source = (PACKAGE / "generator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    attr_calls = {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    model_calls = {c for c in attr_calls if c[0] == "freeroute"}
    assert ("freeroute", "complete") in model_calls
    assert model_calls <= {("freeroute", "complete"), ("freeroute", "redact")}
    assert 'egress="leave"' in source
