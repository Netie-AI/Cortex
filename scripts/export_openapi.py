#!/usr/bin/env python3
"""Emit ``contract/openapi-<CONTRACT_VERSION>.json`` from contract models + routes.

The OpenAPI document is the artifact DMS consumes. Regenerating in CI must
produce no diff (see ``.github/workflows/ci.yml``).

Route surface covers the same FastAPI app served on :8000 (demo) and :8010
(engine) — both ports bind ``CortexOS.api.main:app``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _contract_schemas() -> dict[str, Any]:
    """Build JSON Schema components from cortex_contract pydantic models."""
    from packages.cortex_contract.answer import AskRequest, Answer, Provenance
    from packages.cortex_contract.execution import (
        Manifest,
        PoolSpec,
        QueryResult,
        SubmitRequest,
    )
    from packages.cortex_contract.ledger import ChainVerification, LedgerEntry
    from packages.cortex_contract.proposal import Diff, GateResult, Proposal, ProposalVersion
    from packages.cortex_contract.tools import ToolCall, ToolResult, ToolSpec
    from packages.cortex_contract.version import CONTRACT_VERSION

    models = [
        AskRequest,
        Answer,
        Provenance,
        PoolSpec,
        Manifest,
        SubmitRequest,
        QueryResult,
        LedgerEntry,
        ChainVerification,
        Diff,
        GateResult,
        ProposalVersion,
        Proposal,
        ToolSpec,
        ToolCall,
        ToolResult,
    ]
    schemas: dict[str, Any] = {}
    for model in models:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        # Flatten $defs into components
        defs = schema.pop("$defs", {})
        for name, body in defs.items():
            schemas.setdefault(name, body)
        schemas[model.__name__] = schema
    return CONTRACT_VERSION, schemas


def _app_openapi() -> dict[str, Any]:
    os.environ.setdefault("PACK", "dms")
    os.environ.setdefault("DMS_AUTH_DISABLED", "1")
    # Avoid pulling optional planes off during export when profile is unset.
    os.environ.pop("CORTEX_PROFILE", None)

    from CortexOS.api.app import create_app

    app = create_app()
    return app.openapi()


def build_spec() -> dict[str, Any]:
    contract_version, contract_schemas = _contract_schemas()
    spec = _app_openapi()

    info = dict(spec.get("info") or {})
    info["title"] = "Cortex Engine API"
    info["version"] = contract_version
    info["description"] = (
        "OpenAPI surface for the Cortex engine (ports :8000 demo / :8010 engine) "
        f"plus cortex_contract {contract_version} schemas. "
        "Engine semver is independent (see CortexOS.__version__ / docs/RELEASING.md)."
    )
    info["x-cortex-contract-version"] = contract_version
    info["x-cortex-ports"] = [8000, 8010]
    spec["info"] = info

    components = dict(spec.get("components") or {})
    schemas = dict(components.get("schemas") or {})
    for name, body in contract_schemas.items():
        # Prefix contract models so they never collide with FastAPI-generated names.
        key = name if name.startswith("Contract") else f"Contract{name}"
        schemas[key] = body
    components["schemas"] = schemas
    spec["components"] = components

    # Stable key ordering for drift-free diffs.
    return json.loads(json.dumps(spec, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    contract_version, _ = _contract_schemas()
    out = ROOT / "contract" / f"openapi-{contract_version}.json"
    spec = build_spec()
    rendered = json.dumps(spec, indent=2, sort_keys=True) + "\n"

    if check:
        if not out.is_file():
            print(f"MISSING {out}", file=sys.stderr)
            return 1
        existing = out.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"OpenAPI drift: {out} is stale. Run: python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK — {out.relative_to(ROOT)} matches regeneration")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
