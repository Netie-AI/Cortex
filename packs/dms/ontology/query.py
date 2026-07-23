"""Query CLI over the O2 codebase ontology DB — deterministic SQL, no LLM.

    python -m packs.dms.ontology.query --covers packs/dms/audit/ledger.py
    python -m packs.dms.ontology.query --gate F7
    python -m packs.dms.ontology.query --module packs.dms.audit.ledger

Payoff: the "codebase research" subagent pattern (ARCHITECTURE.md §8) gets cheaper
and more accurate with zero new LLM surface area. Build the DB first with
``python -m scripts.build_codebase_ontology``.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "data" / "codebase_ontology.db"


def _connect(db_path: Path | str | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m scripts.build_codebase_ontology` first"
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _path_to_module(file_arg: str) -> str:
    """Accept a repo-relative path OR a dotted module and normalise to a module key."""
    if "/" in file_arg or "\\" in file_arg or file_arg.endswith(".py"):
        rel = file_arg.replace("\\", "/")
        rel = rel[:-3] if rel.endswith(".py") else rel
        parts = [p for p in rel.split("/") if p]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)
    return file_arg


def covers(module: str, db_path: Path | str | None = None) -> list[str]:
    """Test modules that import the given source module (coverage-by-import)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT test_module FROM link_tests WHERE source_module = ? ORDER BY test_module",
            (module,),
        ).fetchall()
    finally:
        conn.close()
    return [r["test_module"] for r in rows]


def gate(name: str, db_path: Path | str | None = None) -> dict[str, list[str]]:
    """Test modules for a gate, plus the source modules those tests cover."""
    conn = _connect(db_path)
    try:
        tests = [
            r["test_module"]
            for r in conn.execute(
                "SELECT DISTINCT test_module FROM link_implements_gate WHERE gate = ? ORDER BY test_module",
                (name.upper(),),
            ).fetchall()
        ]
        sources: list[str] = []
        if tests:
            marks = ",".join("?" * len(tests))
            sources = [
                r["source_module"]
                for r in conn.execute(
                    f"SELECT DISTINCT source_module FROM link_tests "  # noqa: S608 — marks are placeholders
                    f"WHERE test_module IN ({marks}) ORDER BY source_module",
                    tests,
                ).fetchall()
            ]
    finally:
        conn.close()
    return {"tests": tests, "modules": sources}


def module_info(module: str, db_path: Path | str | None = None) -> dict[str, object]:
    conn = _connect(db_path)
    try:
        mod = conn.execute(
            "SELECT module, path, docstring, parse_ok FROM code_module WHERE module = ?",
            (module,),
        ).fetchone()
        if mod is None:
            return {}
        funcs = [
            {"name": r["name"], "kind": r["kind"], "doc": r["docstring"]}
            for r in conn.execute(
                "SELECT name, kind, docstring FROM code_function WHERE module = ? ORDER BY kind, name",
                (module,),
            ).fetchall()
        ]
        tests = covers(module, db_path=db_path)
    finally:
        conn.close()
    return {
        "module": mod["module"],
        "path": mod["path"],
        "doc": mod["docstring"],
        "functions": funcs,
        "covered_by": tests,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the codebase ontology (O2).")
    parser.add_argument("--db", default=None, help="path to codebase_ontology.db")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--covers", metavar="FILE_OR_MODULE", help="tests that cover a source module")
    g.add_argument("--gate", metavar="GATE", help="tests + modules implementing a gate (e.g. F7)")
    g.add_argument("--module", metavar="MODULE", help="summary of one module")
    args = parser.parse_args(argv)

    if args.covers:
        module = _path_to_module(args.covers)
        tests = covers(module, db_path=args.db)
        print(f"# tests covering {module} ({len(tests)}):")
        for t in tests:
            print(f"  {t}")
    elif args.gate:
        res = gate(args.gate, db_path=args.db)
        print(f"# gate {args.gate.upper()} — {len(res['tests'])} test(s), {len(res['modules'])} module(s):")
        for t in res["tests"]:
            print(f"  test: {t}")
        for m in res["modules"]:
            print(f"  mod:  {m}")
    else:
        info = module_info(args.module, db_path=args.db)
        if not info:
            print(f"# module {args.module} not found")
            return 1
        print(f"# {info['module']}  ({info['path']})")
        if info["doc"]:
            print(f"  {info['doc']}")
        for fn in info["functions"]:
            print(f"  {fn['kind']}: {fn['name']}  — {fn['doc']}")
        print(f"  covered_by: {', '.join(info['covered_by']) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
