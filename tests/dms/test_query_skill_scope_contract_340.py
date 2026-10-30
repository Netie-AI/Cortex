"""#340a import boundary: the packs -> CortexOS ``ask_scope`` edge.

``packs.dms.semantic.query_skills`` imports ``CortexOS.dms.ask_scope``. No
existing contract forbids packs -> CortexOS (contract 1 is engine -> pack), so
the edge is allowed; the named contract ``query-skill-scope`` pins that the
target stays a stdlib leaf that can never reach a pack, the answer plane, an
executor, memory, a model or a DB driver.

``test_must_fail_scope_contract_breaks`` runs the real ``lint-imports`` against
a scratch tree carrying that contract copied verbatim from ``.importlinter``:
each forbidden import must BREAK it and the clean tree (which includes the
packs -> ask_scope edge) must keep it. grimp neither sees function-level
imports nor ``packs/dms/semantic`` (no ``__init__.py``), so the AST tests pin
both sides of the edge on the real files, each with its own must-fail.
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
SECTION = "importlinter:contract:query-skill-scope"
SOURCE = "CortexOS.dms.ask_scope"
PACK_SIDE = "packs.dms.semantic.query_skills"
FORBIDDEN = (
    "packs",
    "CortexOS.api",
    "CortexOS.dms.answer_engine",
    "CortexOS.dms.query_service",
    "CortexOS.execution",
    "CortexOS.memory",
    "CortexOS.integrations",
    "CortexOS.crew",
    "CortexOS.nlp",
    "duckdb",
    "litellm",
    "httpx",
)


def _config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    return parser


def _contract() -> configparser.SectionProxy:
    parser = _config()
    assert parser.has_section(SECTION), f"{SECTION} (#340) missing from .importlinter"
    return parser[SECTION]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _covers(listed: str, module: str) -> bool:
    return module == listed or module.startswith(listed + ".")


def test_scope_contract_is_present_named_and_scoped() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "QUERY-SKILL #340" in section["name"]
    assert _lines(section["source_modules"]) == [SOURCE]
    assert set(_lines(section["forbidden_modules"])) >= set(FORBIDDEN)
    assert "ignore_imports" not in section


def test_no_existing_contract_forbids_the_packs_to_ask_scope_edge() -> None:
    parser = _config()
    contracts = [s for s in parser.sections() if s.startswith("importlinter:contract:")]
    assert {"importlinter:contract:1", SECTION} <= set(contracts)
    for name in contracts:
        section = parser[name]
        if section["type"] == "forbidden":
            sources = _lines(section["source_modules"])
            forbidden = _lines(section["forbidden_modules"])
            assert not (
                any(_covers(s, PACK_SIDE) for s in sources)
                and any(_covers(f, SOURCE) for f in forbidden)
            ), f"{name} forbids {PACK_SIDE} -> {SOURCE}"
        elif section["type"] == "independence":
            modules = _lines(section["modules"]) + _lines(section.get("independent_from", ""))
            assert not (
                any(_covers(m, PACK_SIDE) for m in modules)
                and any(_covers(m, SOURCE) for m in modules)
            ), f"{name} separates {PACK_SIDE} from {SOURCE}"
        else:
            raise AssertionError(f"{name}: contract type {section['type']!r} not checked here")


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _scratch_tree(tmp: Path, offending_import: str | None) -> Path:
    section = _contract()
    for pkg in (
        "CortexOS",
        "CortexOS/api",
        "CortexOS/dms",
        "CortexOS/execution",
        "CortexOS/memory",
        "CortexOS/integrations",
        "CortexOS/crew",
        "CortexOS/nlp",
        "packs",
        "packs/dms",
        "packs/dms/semantic",
    ):
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    for leaf in ("CortexOS/dms/answer_engine.py", "CortexOS/dms/query_service.py"):
        (tmp / leaf).write_text("", encoding="utf-8")
    (tmp / "packs/dms/semantic/query_skills.py").write_text(
        "from CortexOS.dms.ask_scope import current_ask_scope\n", encoding="utf-8"
    )
    body = "from contextvars import ContextVar\n" + (
        f"import {offending_import}\n" if offending_import else ""
    )
    (tmp / "CortexOS/dms/ask_scope.py").write_text(body, encoding="utf-8")
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
    "offending_import",
    [
        "packs.dms.semantic.query_skills",  # the pack back into the engine (a cycle)
        "CortexOS.dms.answer_engine",  # the answer plane into the pack
        "CortexOS.api",  # the HTTP plane
        "CortexOS.execution",  # an executor
        "CortexOS.memory",  # C-MEM store
        "duckdb",  # a DB driver
    ],
)
def test_must_fail_scope_contract_breaks(tmp_path: Path, offending_import: str) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, offending_import))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "QUERY-SKILL #340" in " ".join(out.split()), out
    assert offending_import.split(".")[0] in out, out


def test_scope_contract_keeps_on_clean_tree_with_the_pack_edge(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "KEPT" in out and "1 kept, 0 broken" in out, out


def _imports(source: str) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    return names


def _non_stdlib(source: str) -> list[str]:
    return [
        n for n in _imports(source)
        if n.split(".")[0] not in sys.stdlib_module_names and n != "__future__"
    ]


def _engine_imports(source: str) -> list[str]:
    return [n for n in _imports(source) if n.split(".")[0] in ("CortexOS", "netie")]


def _read(module: str) -> str:
    return (ROOT / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")


def test_ask_scope_imports_only_stdlib_at_any_level() -> None:
    assert _non_stdlib(_read(SOURCE)) == []


def test_pack_side_reaches_the_engine_only_through_ask_scope() -> None:
    assert _engine_imports(_read(PACK_SIDE)) == [SOURCE]


_FN_LEVEL = "\n\ndef _late():\n    from {mod} import x  # noqa\n    return x\n"


@pytest.mark.parametrize("mod", ["packs.dms.semantic.query_skills", "duckdb"])
def test_must_fail_function_level_import_in_ask_scope_is_caught(mod: str) -> None:
    assert _non_stdlib(_read(SOURCE) + _FN_LEVEL.format(mod=mod)) == [mod]


@pytest.mark.parametrize("mod", ["CortexOS.dms.answer_engine", "CortexOS.api.contract_routes"])
def test_must_fail_function_level_engine_import_in_pack_side_is_caught(mod: str) -> None:
    assert _engine_imports(_read(PACK_SIDE) + _FN_LEVEL.format(mod=mod)) == [SOURCE, mod]
