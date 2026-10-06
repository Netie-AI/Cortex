"""C-LOOP (#291) orchestrator boundary: ``CortexOS/loop`` imports only what it may.

The orchestrator takes its generator, executor, self-correct and packager as
injected callables, so its package may import only the stdlib modules below,
``sqlglot``, ``cortex_contract``, ``CortexOS.loop`` and ``CortexOS.memory``. A
model client, a model router, FreeRoute, a pack, a DB driver, the network or
the answer plane is refused — at module level or inside a function (grimp does
not see the latter). The FreeRoute-only import-linter contract is C-LOOP-A's
(#303); this is an AST check on this package only.

``test_must_fail_import_boundary_catches`` proves the checker fails on each
kind of crossing, written the way the real crossings in this repo are written.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOOP_DIR = ROOT / "CortexOS" / "loop"
ALLOWED_STDLIB = frozenset(
    {
        "__future__",
        "collections",
        "contextlib",
        "contextvars",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "math",
        "os",
        "pathlib",
        "re",
        "shutil",
        "sys",
        "tempfile",
        "threading",
        "typing",
    }
)
ALLOWED = ALLOWED_STDLIB | {"sqlglot", "cortex_contract", "CortexOS.loop", "CortexOS.memory"}
_PROVIDER_LITERALS = ("groq", "openai", "anthropic", "gemini", "mistral", "ollama", "gpt-", "claude", "llama")


def _allowed(name: str) -> bool:
    return any(name == a or name.startswith(a + ".") for a in ALLOWED)


def _violations(source: str, where: str = "<src>") -> list[str]:
    bad: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                bad.append(f"{where}: relative import")
            elif node.module:
                names = [node.module]
        elif isinstance(node, ast.Call):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if called in {"__import__", "import_module"}:
                bad.append(f"{where}: dynamic import")
        bad.extend(f"{where}: {name}" for name in names if not _allowed(name))
    return bad


def _loop_sources() -> list[Path]:
    files = sorted(LOOP_DIR.glob("*.py"))
    assert {p.name for p in files} >= {"__init__.py", "interfaces.py", "orchestrator.py", "tools.py", "sandbox.py"}
    return files


def test_loop_package_imports_only_its_allowlist() -> None:
    bad: list[str] = []
    for path in _loop_sources():
        bad.extend(_violations(path.read_text(encoding="utf-8"), path.name))
    assert bad == [], bad


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n    from CortexOS.integrations.freeroute import complete\n",
        "def f():\n    import CortexOS.routing.judgment_model\n",
        "import litellm\n",
        "def f():\n    from packs.dms import brain\n",
        "import duckdb\n",
        "def f():\n    from CortexOS.dms.answer_engine import answer\n",
        "def f():\n    from CortexOS.execution.submit import execute_sql\n",
        "import httpx\n",
        "def f():\n    import socket\n",
        "def f():\n    import importlib\n    importlib.import_module('httpx')\n",
        "def f():\n    __import__('openai')\n",
        "from . import orchestrator\n",
    ],
    ids=[
        "freeroute",
        "model_router",
        "model_client",
        "pack",
        "db_driver",
        "answer_plane",
        "executor",
        "http_client",
        "network",
        "import_module",
        "dunder_import",
        "relative",
    ],
)
def test_must_fail_import_boundary_catches(source: str) -> None:
    assert _violations(source), f"crossing not caught:\n{source}"


def test_import_boundary_keeps_allowed_imports() -> None:
    clean = (
        "from __future__ import annotations\nimport re\nimport sqlglot\n"
        "from cortex_contract.answer import LoopStep\nfrom CortexOS.memory.space_memory import SpaceMemory\n"
        "def f():\n    from CortexOS.loop.tools import default_registry\n"
    )
    assert _violations(clean) == []


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
