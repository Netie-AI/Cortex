"""RSF-06 BAN paste: analog engines must not become Constructor product_engine.

R-0007: the poison fixture under tests/contract/fixtures/ is the check that
this gate can fail. Production distill_harness is scanned separately.
"""

from __future__ import annotations

import ast
from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rsf06_paste_engine.py"
HARNESS = ROOT / "CortexOS" / "execution" / "distill_harness.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(_source(path), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_poison_fixture_still_pastes_analogs() -> None:
    src = _source(FIXTURE)
    hits = _imported_modules(FIXTURE)
    assert "langchain" in hits
    assert "langflow" in hits
    assert "n8n" in hits
    assert "product_engine" in src
    assert "kind" in src
    assert "check_gate" not in src


def test_production_harness_refuses_paste_and_stays_import_clean() -> None:
    src = _source(HARNESS)
    hits = _imported_modules(HARNESS)
    assert "langchain" not in hits
    assert "langflow" not in hits
    assert "n8n" not in hits
    assert "gencfsm" not in hits
    assert "run_distill" in src
    assert "product_engine" in src
    assert "BAN" in src
    assert "will not invent-green COMPLETE" in src
    assert "urlopen" not in src
    assert "127.0.0.1:20128" not in src


def test_importlinter_contract_covers_distill_harness() -> None:
    parser = ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    section = "importlinter:contract:3"
    sources = {
        line.strip()
        for line in parser.get(section, "source_modules").splitlines()
        if line.strip()
    }
    assert "CortexOS.execution.distill_harness" in sources
    forbidden = {
        line.strip()
        for line in parser.get(section, "forbidden_modules").splitlines()
        if line.strip()
    }
    for name in ("n8n", "langchain", "langflow", "gencfsm"):
        assert name in forbidden
