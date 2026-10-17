"""VALUE-NORMALIZE-CX-01 (#387): the published semantics export is the pack declaration.

``contract/semantics/dms.json`` is what DMS vendors to learn the canonical
``transactions.txn_type`` values, the source spellings that map to them, and
the declared measures. It must be exactly what the registry loads from
``packs/dms/ontology/object_types.yaml``: no key invented here, nothing
hand-edited, and a YAML edit without regeneration must fail the check.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from CortexOS.ontology import value_domain as vd
from CortexOS.ontology.registry import load_object_types
from scripts import export_semantics

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "packs" / "dms"
EXPORT = ROOT / "contract" / "semantics" / "dms.json"
DIGEST = ROOT / "contract" / "semantics" / "dms.json.sha256"


def _export() -> dict[str, Any]:
    return json.loads(EXPORT.read_text(encoding="utf-8"))


def test_committed_export_passes_the_check() -> None:
    assert export_semantics.main(["--check"]) == 0


def test_export_equals_what_the_registry_loads() -> None:
    doc = _export()
    assert doc["pack"] == "dms"
    assert doc["source"] == "packs/dms/ontology/object_types.yaml"
    want_domains = [
        {
            "table": d.table,
            "column": d.column,
            "canonical": list(d.canonical),
            "from": d.mapping(),
        }
        for d in vd.load_value_domains(PACK)
    ]
    assert doc["value_domains"] == want_domains
    want_measures = [
        {
            "table": o.id,
            "name": m.name,
            "description": m.description,
            "expression": m.expression,
            "filter": {"column": m.filter_column, "equals": m.filter_value},
            "grain": m.grain,
            "metrics": list(m.metrics),
        }
        for o in load_object_types(PACK)
        for m in o.measures
    ]
    assert doc["measures"] == want_measures
    # Not vacuous: the txn_type domain and both sales measures are published.
    txn = next(d for d in doc["value_domains"] if d["column"] == "txn_type")
    assert txn["canonical"][:2] == ["outbound", "inbound"]
    assert txn["from"] == {"OUT": "outbound", "IN": "inbound"}
    assert {m["name"] for m in doc["measures"]} == {"sales_value_myr", "sales_volume_kg"}


def test_export_carries_only_what_the_yaml_states() -> None:
    """Every published value is read off the raw YAML, independent of the loader."""
    raw = yaml.safe_load((PACK / "ontology" / "object_types.yaml").read_text(encoding="utf-8"))
    doc = _export()
    assert set(doc) == {"pack", "source", "source_sha256", "value_domains", "measures"}
    src = (PACK / "ontology" / "object_types.yaml").read_bytes().replace(b"\r\n", b"\n")
    assert doc["source_sha256"] == hashlib.sha256(src).hexdigest()

    raw_domains = []
    raw_measures = []
    for obj in raw["object_types"]:
        for prop in obj.get("properties") or []:
            if prop.get("values"):
                raw_domains.append((obj["id"], prop["name"], prop["values"]))
        for m in obj.get("measures") or []:
            raw_measures.append((obj["id"], m))
    assert len(doc["value_domains"]) == len(raw_domains)
    for pub, (table, column, values) in zip(doc["value_domains"], raw_domains, strict=True):
        assert set(pub) == {"table", "column", "canonical", "from"}
        assert (pub["table"], pub["column"]) == (table, column)
        assert pub["canonical"] == [v["canonical"] for v in values]
        assert pub["from"] == {s: v["canonical"] for v in values for s in v.get("from", [])}
    assert len(doc["measures"]) == len(raw_measures)
    for pub, (table, m) in zip(doc["measures"], raw_measures, strict=True):
        assert set(pub) == {
            "table", "name", "description", "expression", "filter", "grain", "metrics",
        }
        assert pub["table"] == table
        assert pub["name"] == m["name"]
        assert pub["expression"] == m["expression"].strip()
        assert pub["filter"] == m["filter"]
        assert pub["grain"] == m["grain"]
        assert pub["metrics"] == m["metrics"]
        assert pub["description"] == m.get("description", "")


def test_digest_sidecar_matches_the_export() -> None:
    body = EXPORT.read_bytes().replace(b"\r\n", b"\n")
    recorded = DIGEST.read_text(encoding="utf-8").split()
    assert recorded == [hashlib.sha256(body).hexdigest(), "dms.json"]


# ── must-fail: a declaration change without regeneration is drift ──────────


def _pack_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "packs" / "dms"
    shutil.copytree(PACK / "ontology", dst / "ontology")
    return dst


def _edit_yaml(pack: Path, old: str, new: str) -> None:
    path = pack / "ontology" / "object_types.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_unchanged_copy_of_the_pack_checks_clean(tmp_path: Path) -> None:
    """Control for the must-fail cases below: the copy itself is not drift."""
    pack = _pack_copy(tmp_path)
    assert export_semantics.main(["--check", "--pack-dir", str(pack)]) == 0


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # A changed canonical value changes meaning.
        ("{canonical: outbound, from: [OUT]}", "{canonical: sale, from: [OUT]}"),
        # An added source spelling is additive, but still a new export.
        ("{canonical: inbound, from: [IN]}", "{canonical: inbound, from: [IN, RECEIPT]}"),
    ],
)
def test_yaml_edit_without_regeneration_fails_the_check(
    tmp_path: Path, old: str, new: str, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = _pack_copy(tmp_path)
    if "sale" in new:
        # The measures filter on 'outbound'; keep the copy loadable.
        path = pack / "ontology" / "object_types.yaml"
        text = path.read_text(encoding="utf-8").replace("equals: outbound", "equals: sale")
        path.write_text(text, encoding="utf-8")
    _edit_yaml(pack, old, new)
    assert export_semantics.main(["--check", "--pack-dir", str(pack)]) == 1
    assert "Semantics drift" in capsys.readouterr().err


def test_regenerating_after_the_edit_publishes_the_new_spelling(tmp_path: Path) -> None:
    pack = _pack_copy(tmp_path)
    _edit_yaml(pack, "{canonical: inbound, from: [IN]}", "{canonical: inbound, from: [IN, RECEIPT]}")
    out = tmp_path / "out"
    assert export_semantics.main(["--pack-dir", str(pack), "--out-dir", str(out)]) == 0
    assert export_semantics.main(["--check", "--pack-dir", str(pack), "--out-dir", str(out)]) == 0
    doc = json.loads((out / "dms.json").read_text(encoding="utf-8"))
    txn = next(d for d in doc["value_domains"] if d["column"] == "txn_type")
    assert txn["from"] == {"OUT": "outbound", "IN": "inbound", "RECEIPT": "inbound"}


def test_tampered_digest_fails_the_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert export_semantics.main(["--out-dir", str(out)]) == 0
    (out / "dms.json.sha256").write_text("0" * 64 + "  dms.json\n", encoding="utf-8")
    assert export_semantics.main(["--check", "--out-dir", str(out)]) == 1
    assert "digest drift" in capsys.readouterr().err


def test_ambiguous_declaration_is_never_published(tmp_path: Path) -> None:
    """The export goes through the loader: a spelling mapped two ways is refused."""
    pack = _pack_copy(tmp_path)
    _edit_yaml(pack, "{canonical: inbound, from: [IN]}", "{canonical: inbound, from: [IN, OUT]}")
    with pytest.raises(ValueError, match="maps to two values"):
        export_semantics.main(["--pack-dir", str(pack), "--out-dir", str(tmp_path / "out")])
    assert not (tmp_path / "out" / "dms.json").exists()
