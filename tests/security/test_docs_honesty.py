"""Recon #9 -- live docs must not claim a wire or control the tree does not run.

WASM row, contract surface, STATUS single header, test baseline >=330.
DOC-01 already gates README isolation claims; this gates the operator map.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _contract_version() -> str:
    ns: dict[str, str] = {}
    exec(
        (ROOT / "packages" / "cortex_contract" / "version.py").read_text(
            encoding="utf-8"
        ),
        ns,
    )
    return ns["CONTRACT_VERSION"]


def test_wasm_isolate_module_is_gone() -> None:
    """DOC-01 deleted the unused scaffold. Citing it implies a live sandbox."""
    assert not (ROOT / "CortexOS" / "execution" / "wasm_isolate.py").exists()


def test_architecture_wasm_row_is_not_built() -> None:
    arch = _text("ARCHITECTURE.md")
    after = arch.split("## 3. Not built", 1)[1].split("## 4.", 1)[0]
    assert "Production WASM" in after


def test_truth_map_does_not_claim_wasm_scaffold() -> None:
    truth = _text("docs", "dms", "TRUTH_GROUND_MAP.md")
    assert "wasm (scaffold)" not in truth
    assert "wasm_isolate" not in truth


def test_f8_packet_does_not_cite_deleted_wasm_isolate() -> None:
    packet = _text("docs", "dms", "GATE_F8_PACKET.md")
    assert "wasm_isolate.py" not in packet


def test_contract_surface_matches_packaged_version() -> None:
    ver = _contract_version()
    spec = f"contract/openapi-{ver}.json"
    releasing = _text("docs", "RELEASING.md")
    contract_row = next(
        ln for ln in releasing.splitlines() if "| **Contract**" in ln
    )
    assert f"`{ver}`" in contract_row, contract_row
    arch = _text("ARCHITECTURE.md")
    assert spec in arch
    assert "/v1/contract/" in arch
    readme = _text("README.md")
    assert spec in readme


def test_status_has_one_current_last_updated_header() -> None:
    headers = [
        ln
        for ln in _text("STATUS.md").splitlines()
        if ln.startswith("**Last updated:**")
    ]
    assert len(headers) == 1, headers


def test_status_test_baseline_floor_is_at_least_330() -> None:
    status = _text("STATUS.md")
    assert ">=330" in status.replace(" ", "")


def test_context_153_count_is_marked_historical() -> None:
    ctx = _text("CONTEXT.md")
    if "153 passed" not in ctx:
        return
    assert "HISTORICAL" in ctx
