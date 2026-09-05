"""Constructor engine must not import n8n/langchain/langflow/gencfsm as engine.

R-0007: the poison fixture under tests/contract/fixtures/ is the check that
this gate can fail. Production CortexOS is scanned separately and must stay
clean. Upstream trees are not vendored.
"""

from __future__ import annotations

import ast
from configparser import ConfigParser
from pathlib import Path

from CortexOS.execution.distill_options import (
    BANNED_ENGINE_IMPORT_PREFIXES,
    REQUIRED_OPTION_IDS,
    is_banned_engine_import,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "rsf01_banned_engine_import.py"
CONSTRUCTOR_ENGINE_PATHS = (
    ROOT / "CortexOS" / "constructor_graph.py",
    ROOT / "CortexOS" / "execution" / "distill_options.py",
    ROOT / "CortexOS" / "execution" / "rsf_boundary.py",
    ROOT / "packs" / "dms" / "constructor_routes.py",
    ROOT / "packs" / "dms" / "constructor_fetch.py",
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _violations(path: Path) -> list[str]:
    return [mod for mod in _imported_modules(path) if is_banned_engine_import(mod)]


def test_ban_prefixes_cover_required_analogs() -> None:
    locked = {prefix.split("_", 1)[0] for prefix in BANNED_ENGINE_IMPORT_PREFIXES}
    assert {"n8n", "langchain", "langflow", "gencfsm"} <= locked


def test_poison_fixture_is_caught() -> None:
    """If BAN is removed, this assertion fails — the fixture still imports analogs."""
    hits = _violations(FIXTURE)
    assert "n8n" in hits
    assert "langchain" in hits
    assert "langflow" in hits
    assert "gencfsm" in hits
    assert any(is_banned_engine_import(mod) for mod in hits)


def test_constructor_engine_modules_have_no_banned_imports() -> None:
    offenders: list[str] = []
    for path in CONSTRUCTOR_ENGINE_PATHS:
        if not path.is_file():
            offenders.append(f"{path.as_posix()} (missing)")
            continue
        offenders.extend(f"{path.relative_to(ROOT).as_posix()}:{mod}" for mod in _violations(path))
    assert not offenders, "Constructor engine imported a banned analog:\n" + "\n".join(offenders)


def test_cortex_gen_cfsm_is_not_a_banned_import() -> None:
    assert not is_banned_engine_import("CortexOS.execution.gen_cfsm")
    assert not is_banned_engine_import("CortexOS.execution.gen_cfsm.compile_ir")


def test_importlinter_contract_forbids_banned_engines() -> None:
    parser = ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    section = "importlinter:contract:3"
    assert parser.has_section(section), "missing import-linter Constructor engine BAN contract"
    forbidden = {
        line.strip()
        for line in parser.get(section, "forbidden_modules").splitlines()
        if line.strip()
    }
    for name in ("n8n", "langchain", "langflow", "gencfsm"):
        assert name in forbidden, f"{name} missing from import-linter contract 3"
    sources = {
        line.strip()
        for line in parser.get(section, "source_modules").splitlines()
        if line.strip()
    }
    assert "CortexOS.constructor_graph" in sources
    assert "CortexOS.execution.distill_options" in sources
    assert "CortexOS.execution.rsf_boundary" in sources
    assert parser.getboolean("importlinter", "include_external_packages") is True


def test_required_option_ids_cannot_be_product_engine() -> None:
    from CortexOS.execution.distill_options import catalog

    by_id = {row["id"]: row for row in catalog()}
    for option_id in REQUIRED_OPTION_IDS:
        assert by_id[option_id]["engine_role"] != "product_engine"
        assert by_id[option_id]["engine_role"] in {"distill_only", "compete", "learn"}
