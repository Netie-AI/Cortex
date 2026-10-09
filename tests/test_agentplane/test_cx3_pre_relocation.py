"""CX-3-pre (#310): engine code never imports ``CortexOS.crew``.

The crew modules engine code reached now live in ``CortexOS.agentplane``. The
only ``CortexOS.crew -> CortexOS.agentplane`` edges left are the named
re-export shims, which CX-3 deletes with the crew server. Contract 5 in
``.importlinter`` is the lint gate; the AST scan here also covers namespace
directories grimp does not treat as modules. Both are proven able to fail.
"""

from __future__ import annotations

import ast
import configparser
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.test_crew import cx3_pre_310_license as license_310

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "CortexOS"
CREW = ENGINE / "crew"
SECTION = "importlinter:contract:5"
SHIMS = frozenset(f"CortexOS/crew/{name}.py" for name in license_310.MODULES)


def _module_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: Path, root: Path) -> list[tuple[int, str]]:
    """Every module an import statement names, at any nesting level."""
    package = _module_name(path, root).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            found.append((node.lineno, module))
            found.extend((node.lineno, f"{module}.{alias.name}") for alias in node.names)
    return found


def _crossings(files: list[Path], root: Path, target: str) -> set[str]:
    return {
        f"{path.relative_to(root).as_posix()}:{line} -> {name}"
        for path in files
        for line, name in _imports(path, root)
        if name == target or name.startswith(target + ".")
    }


def _engine_files(engine: Path) -> list[Path]:
    crew = engine / "crew"
    return sorted(p for p in engine.rglob("*.py") if crew not in p.parents)


def test_engine_never_imports_crew_at_any_level() -> None:
    assert _crossings(_engine_files(ENGINE), ROOT, "CortexOS.crew") == set()


def test_engine_scan_catches_function_level_and_relative_crossings(tmp_path: Path) -> None:
    engine = tmp_path / "CortexOS"
    for pkg in ("crew", "insights", "execution"):
        (engine / pkg).mkdir(parents=True)
        (engine / pkg / "__init__.py").write_text("", encoding="utf-8")
    (engine / "__init__.py").write_text("", encoding="utf-8")
    (engine / "insights" / "routes.py").write_text(
        "def body():\n    from CortexOS.crew import insights\n", encoding="utf-8"
    )
    (engine / "execution" / "scheduled_work.py").write_text(
        "def run():\n    from ..crew.github import list_prs\n", encoding="utf-8"
    )
    (engine / "crew" / "server.py").write_text(
        "from CortexOS.crew import insights\n", encoding="utf-8"
    )
    assert _crossings(_engine_files(engine), tmp_path, "CortexOS.crew") == {
        "CortexOS/insights/routes.py:2 -> CortexOS.crew",
        "CortexOS/insights/routes.py:2 -> CortexOS.crew.insights",
        "CortexOS/execution/scheduled_work.py:2 -> CortexOS.crew.github",
        "CortexOS/execution/scheduled_work.py:2 -> CortexOS.crew.github.list_prs",
    }


def test_crew_reaches_agentplane_only_through_the_named_shims() -> None:
    crew_files = sorted(CREW.glob("*.py"))
    bridges = {
        hit.split(":", 1)[0] for hit in _crossings(crew_files, ROOT, "CortexOS.agentplane")
    }
    assert bridges == SHIMS
    for name in sorted(license_310.MODULES):
        shim = (CREW / f"{name}.py").read_text(encoding="utf-8")
        assert shim == license_310.SHIM.format(name=name), name
        old = importlib.import_module(f"CortexOS.crew.{name}")
        assert old is importlib.import_module(f"CortexOS.agentplane.{name}"), name


def _contract() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(SECTION), f"{SECTION} (CX-3-pre) missing from .importlinter"
    return parser[SECTION]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_contract_5_covers_every_engine_child_except_crew() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "CX-3-pre #310" in section["name"]
    assert _lines(section["forbidden_modules"]) == ["CortexOS.crew"]
    assert "ignore_imports" not in section
    children = {
        f"CortexOS.{p.stem}"
        for p in ENGINE.iterdir()
        if (p.is_dir() and (p / "__init__.py").is_file())
        or (p.suffix == ".py" and p.name != "__init__.py")
    }
    assert set(_lines(section["source_modules"])) == children - {"CortexOS.crew"}


def _lint_imports() -> str:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return found


def _scratch_tree(tmp: Path, offending: str | None) -> Path:
    section = _contract()
    for module in [*_lines(section["source_modules"]), "CortexOS.crew"]:
        pkg = tmp.joinpath(*module.split("."))
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS" / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/crew/insights.py").write_text("", encoding="utf-8")
    (tmp / "CortexOS/agentplane/insights.py").write_text("", encoding="utf-8")
    body = "def body():\n    from CortexOS.agentplane import insights\n"
    if offending:
        body += f"    from {offending} import insights as crew_insights\n"
    (tmp / "CortexOS/insights/routes.py").write_text(body, encoding="utf-8")
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
    return subprocess.run(
        [_lint_imports(), "--config", str(config), "--no-cache"],
        cwd=tmp,
        env={**os.environ, "PYTHONPATH": str(tmp)},
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def test_contract_5_breaks_on_function_level_engine_to_crew_import(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, "CortexOS.crew"))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out, out
    assert "CortexOS.insights.routes -> CortexOS.crew" in out, out


def test_contract_5_keeps_on_clean_tree(tmp_path: Path) -> None:
    proc = _run(tmp_path, _scratch_tree(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 kept, 0 broken" in out, out
