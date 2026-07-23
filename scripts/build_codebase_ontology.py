"""Build a queryable codebase knowledge map (Ontology O2) with pure static analysis.

Walk the source trees with Python's ``ast`` module — ZERO LLM calls, so it is free
to run as often as needed and fits the local-first cost discipline by construction.
Writes a SEPARATE ``data/codebase_ontology.db`` (deliberately NOT the ops DB from O1:
this is developer/repo metadata with a different lifecycle — safe to delete and rebuild
without touching product data).

Object types:
  code_module    — one row per .py file (module dotted path, docstring, path)
  code_function  — one row per top-level def/class (name, kind, docstring, module)
Link types:
  imports        — module A imports module B (best-effort dotted target)
  tests          — a tests/**/test_*.py module imports a source module (coverage-by-import)
  implements_gate— a test file name maps to a STATUS.md-style gate token (test_f7_* -> F7)

Run:  python -m scripts.build_codebase_ontology   (or  python scripts/build_codebase_ontology.py)
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("CortexOS", "packs", "netie", "tests")
DEFAULT_DB = ROOT / "data" / "codebase_ontology.db"

# Gate tokens as they appear in STATUS.md / test names: a letter + digit (f7, o1, v0, l0, q1, s0, s1, b3...).
_GATE_TOKEN = re.compile(r"(?<![a-z0-9])([a-z]\d+)(?![a-z0-9])")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_module (
    module TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    docstring TEXT NOT NULL DEFAULT '',
    parse_ok INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS code_function (
    module TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,               -- 'function' | 'class'
    docstring TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (module, name, kind)
);
CREATE TABLE IF NOT EXISTS link_imports (
    from_module TEXT NOT NULL,
    to_target TEXT NOT NULL,          -- dotted import target as written (may be stdlib/third-party)
    PRIMARY KEY (from_module, to_target)
);
CREATE TABLE IF NOT EXISTS link_tests (
    test_module TEXT NOT NULL,
    source_module TEXT NOT NULL,      -- internal source module the test imports
    PRIMARY KEY (test_module, source_module)
);
CREATE TABLE IF NOT EXISTS link_implements_gate (
    test_module TEXT NOT NULL,
    gate TEXT NOT NULL,               -- 'F7', 'O1', ...
    PRIMARY KEY (test_module, gate)
);
"""


def module_name(path: Path) -> str:
    """Repo-relative dotted module path. Package __init__.py -> the package name."""
    rel = path.resolve().relative_to(ROOT)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip .py
    return ".".join(parts)


def _first_doc(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for d in SOURCE_DIRS:
        base = ROOT / d
        if base.exists():
            files.extend(sorted(base.rglob("*.py")))
    return files


def _known_internal_prefixes(modules: set[str]) -> tuple[str, ...]:
    # Any import whose dotted head is a top-level source dir counts as internal.
    return SOURCE_DIRS


def _is_internal(target: str) -> bool:
    head = target.split(".", 1)[0]
    return head in SOURCE_DIRS


def gate_tokens(test_stem: str) -> set[str]:
    """Extract gate tokens from a test file stem, e.g. test_b3_f7_remainder -> {B3, F7}."""
    body = test_stem[len("test_"):] if test_stem.startswith("test_") else test_stem
    return {m.group(1).upper() for m in _GATE_TOKEN.finditer(body)}


def _import_targets(tree: ast.AST) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — cannot resolve without package context, skip
                continue
            if node.module:
                targets.add(node.module)
                for alias in node.names:
                    targets.add(f"{node.module}.{alias.name}")
    return targets


def build(db_path: Path | str = DEFAULT_DB) -> dict[str, int]:
    """Analyze the source trees and (re)build the codebase ontology DB. Idempotent."""
    files = _iter_py_files()

    modules: list[tuple[str, str, str, int]] = []       # module, path, docstring, parse_ok
    functions: list[tuple[str, str, str, str]] = []     # module, name, kind, docstring
    imports: list[tuple[str, str]] = []                 # from_module, to_target
    module_of_path: dict[Path, str] = {}

    for path in files:
        mod = module_name(path)
        module_of_path[path] = mod
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            # Parallel in-progress edits or unreadable files must not break the build.
            modules.append((mod, str(path.relative_to(ROOT)).replace("\\", "/"), "", 0))
            continue

        modules.append(
            (mod, str(path.relative_to(ROOT)).replace("\\", "/"), _first_doc(tree), 1)
        )
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append((mod, node.name, "function", _first_doc(node)))
            elif isinstance(node, ast.ClassDef):
                functions.append((mod, node.name, "class", _first_doc(node)))
        for target in _import_targets(tree):
            imports.append((mod, target))

    all_modules = {m[0] for m in modules}

    # tests + implements_gate links, derived from test modules
    test_links: list[tuple[str, str]] = []
    gate_links: list[tuple[str, str]] = []
    for path, mod in module_of_path.items():
        stem = path.stem
        if not stem.startswith("test_"):
            continue
        for gate in gate_tokens(stem):
            gate_links.append((mod, gate))
        # coverage-by-import: which internal source modules does this test pull in?
        for from_mod, target in imports:
            if from_mod != mod or not _is_internal(target):
                continue
            # map an import target onto the closest known source module
            resolved = target if target in all_modules else target.rsplit(".", 1)[0]
            if resolved in all_modules and not resolved.startswith("tests"):
                test_links.append((mod, resolved))

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        with conn:
            for tbl in ("code_module", "code_function", "link_imports", "link_tests", "link_implements_gate"):
                conn.execute(f"DELETE FROM {tbl}")  # noqa: S608 — fixed table names
            conn.executemany(
                "INSERT OR REPLACE INTO code_module (module, path, docstring, parse_ok) VALUES (?, ?, ?, ?)",
                modules,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO code_function (module, name, kind, docstring) VALUES (?, ?, ?, ?)",
                functions,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO link_imports (from_module, to_target) VALUES (?, ?)",
                sorted(set(imports)),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO link_tests (test_module, source_module) VALUES (?, ?)",
                sorted(set(test_links)),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO link_implements_gate (test_module, gate) VALUES (?, ?)",
                sorted(set(gate_links)),
            )
    finally:
        conn.close()

    return {
        "modules": len(modules),
        "functions": len(functions),
        "imports": len(set(imports)),
        "test_links": len(set(test_links)),
        "gate_links": len(set(gate_links)),
        "unparseable": sum(1 for m in modules if m[3] == 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the codebase ontology DB (static ast analysis).")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="output SQLite path")
    args = parser.parse_args(argv)
    counts = build(args.db)
    print(
        "codebase ontology built: "
        + ", ".join(f"{k}={v}" for k, v in counts.items())
        + f"  -> {args.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
