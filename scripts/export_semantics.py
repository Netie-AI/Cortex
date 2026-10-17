#!/usr/bin/env python3
"""Emit ``contract/semantics/<pack>.json``: the pack's declared value domains and measures.

VALUE-NORMALIZE-CX-01 (#387). What ``transactions.txn_type`` values mean
(``OUT`` -> ``outbound``) and what a sales number means are declared once, as
data, in ``packs/<pack>/ontology/object_types.yaml``. Cortex reads that file
through ``CortexOS.ontology.registry``. DMS may not import ``CortexOS`` and has
no checkout of ``packs/``, so this script publishes the same declaration as a
generated file DMS vendors, the way it vendors ``contract/testvectors/``.

* The export is produced by the registry loader, so a declaration the loader
  refuses (one spelling mapped to two values, a source spelling that is also
  canonical, a measure filter on an undeclared value) is never published.
* Every key is read from the YAML; nothing here knows a value.
* ``--check`` exits 1 when the committed export or its ``.sha256`` sidecar is
  not what regeneration produces. CI runs it beside ``export_openapi --check``.

Like everything under ``contract/``: generated, never hand-edited, never
deleted. Adding a ``from`` spelling is additive; changing a canonical value
changes meaning and is a coordinated change with DMS.

Run: ``python scripts/export_semantics.py [--check] [--pack dms]``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Same reason as export_openapi.py: validate the checkout this script lives in.
if str(ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(ROOT))

from CortexOS.ontology.registry import load_object_types, pack_dir_for  # noqa: E402

OUT_DIR = ROOT / "contract" / "semantics"
SOURCE = Path("ontology") / "object_types.yaml"


def _lf(data: bytes) -> bytes:
    """Hash and compare with LF line ends, so a CRLF checkout is not drift."""
    return data.replace(b"\r\n", b"\n")


def build(pack: str, pack_dir: Path) -> dict[str, Any]:
    """The export document for ``pack``, read through the registry loader."""
    source = pack_dir / SOURCE
    value_domains: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    for obj in load_object_types(pack_dir):
        for prop in obj.properties:
            if prop.values:
                value_domains.append(
                    {
                        "table": obj.id,
                        "column": prop.name,
                        "canonical": [v.canonical for v in prop.values],
                        "from": {s: v.canonical for v in prop.values for s in v.sources},
                    }
                )
        for m in obj.measures:
            measures.append(
                {
                    "table": obj.id,
                    "name": m.name,
                    "description": m.description,
                    "expression": m.expression,
                    "filter": {"column": m.filter_column, "equals": m.filter_value},
                    "grain": m.grain,
                    "metrics": list(m.metrics),
                }
            )
    return {
        "pack": pack,
        "source": f"packs/{pack}/{SOURCE.as_posix()}",
        "source_sha256": hashlib.sha256(_lf(source.read_bytes())).hexdigest(),
        "value_domains": value_domains,
        "measures": measures,
    }


def render(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Emit contract/semantics/<pack>.json")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed export drifted")
    ap.add_argument("--pack", default="dms", help="pack name (default: dms)")
    ap.add_argument("--pack-dir", type=Path, help="read this pack directory instead")
    ap.add_argument("--out-dir", type=Path, help="write or check here instead of contract/semantics")
    args = ap.parse_args(argv)

    pack_dir = args.pack_dir if args.pack_dir is not None else pack_dir_for(args.pack)
    out_dir = args.out_dir if args.out_dir is not None else OUT_DIR
    out = out_dir / f"{args.pack}.json"
    digest = out_dir / f"{args.pack}.json.sha256"

    rendered = render(build(args.pack, pack_dir))
    expected = hashlib.sha256(rendered).hexdigest()

    if args.check:
        if not out.is_file():
            print(f"MISSING {out}", file=sys.stderr)
            return 1
        if _lf(out.read_bytes()) != rendered:
            print(
                f"Semantics drift: {out} is not what {pack_dir / SOURCE} declares. "
                "Run: python scripts/export_semantics.py",
                file=sys.stderr,
            )
            return 1
        if not digest.is_file():
            print(f"MISSING {digest}", file=sys.stderr)
            return 1
        recorded = digest.read_text(encoding="utf-8").split()[0].strip()
        if recorded != expected:
            print(
                f"Semantics digest drift: {digest.name} records {recorded[:12]}, "
                f"export hashes to {expected[:12]}",
                file=sys.stderr,
            )
            return 1
        print(f"OK - {out.name} matches regeneration, digest agrees")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rendered)
    digest.write_bytes(f"{expected}  {out.name}\n".encode())
    print(f"Wrote {out} and {digest.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
