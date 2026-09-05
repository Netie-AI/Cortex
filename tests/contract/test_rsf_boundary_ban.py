"""RSF-03 BAN floors: skip OpenVault, silent analog engine, OmniRoute vendor.

R-0007: the poison fixture under tests/contract/fixtures/ is the check that
this gate can fail. Production CortexOS/execution/rsf_boundary.py is scanned
separately and must keep the leave-machine call.
"""

from __future__ import annotations

import ast
from configparser import ConfigParser
from pathlib import Path

from CortexOS.execution.rsf_boundary import BAN_FLOORS

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rsf03_skip_openvault.py"
BOUNDARY = ROOT / "CortexOS" / "execution" / "rsf_boundary.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_ban_floors_are_explicit() -> None:
    blob = " ".join(BAN_FLOORS).lower()
    assert "skip openvault" in blob
    assert "silent upstream engine" in blob
    assert "product_engine" in blob
    assert "20128" in blob


def test_poison_fixture_still_skips_openvault_and_promotes() -> None:
    """If BAN assertions are deleted, this fixture still encodes the forbidden path."""
    src = _source(FIXTURE)
    assert "urlopen" in src
    assert "product_engine" in src
    assert "check_gate" not in src
    assert "urlopen" in _imported_modules(FIXTURE) or "urllib.request" in _imported_modules(FIXTURE)


def test_production_research_egress_calls_leave_machine() -> None:
    src = _source(BOUNDARY)
    assert "check_gate" in src
    assert 'action="leave"' in src
    assert 'destination="freeroute"' in src
    assert "urlopen" not in src
    assert "127.0.0.1:20128" not in src
    assert "CortexOS.integrations.openvault_gate" in src


def test_importlinter_contract_covers_rsf_boundary() -> None:
    parser = ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    section = "importlinter:contract:3"
    sources = {
        line.strip()
        for line in parser.get(section, "source_modules").splitlines()
        if line.strip()
    }
    assert "CortexOS.execution.rsf_boundary" in sources
    forbidden = {
        line.strip()
        for line in parser.get(section, "forbidden_modules").splitlines()
        if line.strip()
    }
    for name in ("n8n", "langchain", "langflow"):
        assert name in forbidden
