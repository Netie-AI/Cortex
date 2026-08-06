#!/usr/bin/env python3
"""Emit ``contract/openapi-<CONTRACT_VERSION>.json`` from the contract route allowlist.

The OpenAPI document is the artifact DMS consumes. Regenerating in CI must
produce no diff (see ``.github/workflows/ci.yml``).

Contract surface is **exactly** the allowlisted operationIds — never the full
engine route table, and never a profile-dependent subset. Export always demands
the ``[full]`` install so missing extras fail loudly instead of emitting a
smaller silent spec.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Keep in lockstep with CortexOS.api.contract_routes.CONTRACT_ROUTE_IDS.
CONTRACT_ROUTE_IDS: frozenset[str] = frozenset(
    {
        "ask",
        "submit",
        "ledger.append",
        "ledger.verify",
        "tool.registry",
        "drillthrough",
    }
)

_FULL_PROBE_MODULES: tuple[str, ...] = (
    "dbos",
    "qdrant_client",
    "tantivy",
    "sentence_transformers",
)


def _require_full_install() -> None:
    """Fail loudly if optional extras that ``[full]`` provides are missing."""
    missing = [
        name
        for name in _FULL_PROBE_MODULES
        if __import__("importlib.util").util.find_spec(name) is None
    ]
    if missing:
        print(
            "OpenAPI export requires pip install '.[full]' — missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        raise SystemExit(2)


def _contract_schemas() -> tuple[str, dict[str, Any]]:
    """Build JSON Schema components from cortex_contract pydantic models.

    Returns the contract version alongside the schemas so callers stamp the
    spec with the contract line, never the engine line.
    """
    from cortex_contract.answer import (
        Answer,
        AskRequest,
        ContributingSource,
        DrillthroughRequest,
        DrillthroughResponse,
        Provenance,
    )
    from cortex_contract.execution import (
        Manifest,
        PoolSpec,
        QueryResult,
        SubmitRequest,
    )
    from cortex_contract.ledger import ChainVerification, LedgerEntry
    from cortex_contract.proposal import Diff, GateResult, Proposal, ProposalVersion
    from cortex_contract.tools import ToolCall, ToolResult, ToolSpec
    from cortex_contract.version import CONTRACT_VERSION
    from pydantic import BaseModel

    # Annotated as the base class, not inferred: pydantic models are instances of
    # ModelMetaclass, so an unannotated list infers that metaclass and mypy
    # cannot see model_json_schema on it.
    models: list[type[BaseModel]] = [
        AskRequest,
        Answer,
        Provenance,
        ContributingSource,
        DrillthroughRequest,
        DrillthroughResponse,
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
        defs = schema.pop("$defs", {})
        for name, body in defs.items():
            schemas.setdefault(name, body)
        schemas[model.__name__] = schema
    return CONTRACT_VERSION, schemas


def _operation_ids(spec: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for path_item in (spec.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.startswith("x-") or not isinstance(op, dict):
                continue
            oid = op.get("operationId")
            if isinstance(oid, str) and oid:
                found.add(oid)
    return found


def _filter_contract_paths(spec: dict[str, Any]) -> dict[str, Any]:
    """Keep only path operations whose operationId is in the allowlist."""
    paths_out: dict[str, Any] = {}
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        kept: dict[str, Any] = {}
        for method, op in path_item.items():
            if method.startswith("x-"):
                kept[method] = op
                continue
            if isinstance(op, dict) and op.get("operationId") in CONTRACT_ROUTE_IDS:
                kept[method] = op
        # Drop path-level params-only leftovers with no operations.
        ops = [k for k in kept if not k.startswith("x-") and k != "parameters"]
        if ops:
            paths_out[path] = kept
    filtered = dict(spec)
    filtered["paths"] = paths_out
    return filtered


def _assert_exact_route_set(spec: dict[str, Any]) -> None:
    found = _operation_ids(spec)
    missing = CONTRACT_ROUTE_IDS - found
    unexpected = found - CONTRACT_ROUTE_IDS
    if missing or unexpected:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={sorted(missing)}")
        if unexpected:
            parts.append(f"unexpected={sorted(unexpected)}")
        print(
            "Contract OpenAPI route allowlist mismatch: " + "; ".join(parts),
            file=sys.stderr,
        )
        raise SystemExit(3)


def _app_openapi() -> dict[str, Any]:
    os.environ.setdefault("PACK", "dms")
    os.environ.setdefault("DMS_AUTH_DISABLED", "1")
    # Full profile for export — never emit a core-shrunk route table.
    os.environ["CORTEX_PROFILE"] = "full"
    os.environ.setdefault("CORTEX_REQUIRE_AGENTIC_MARKER", "1")

    from CortexOS.api.app import create_app

    app = create_app()
    return app.openapi()


def build_spec() -> dict[str, Any]:
    _require_full_install()
    contract_version, contract_schemas = _contract_schemas()
    spec = _filter_contract_paths(_app_openapi())
    _assert_exact_route_set(spec)

    info = dict(spec.get("info") or {})
    info["title"] = "Cortex Engine Contract API"
    info["version"] = contract_version
    info["description"] = (
        "Allowlisted DMS↔engine contract surface "
        f"(operationIds: {', '.join(sorted(CONTRACT_ROUTE_IDS))}). "
        f"cortex_contract {contract_version}. "
        "Engine semver is independent (see CortexOS.__version__ / docs/RELEASING.md)."
    )
    info["x-cortex-contract-version"] = contract_version
    info["x-cortex-contract-routes"] = sorted(CONTRACT_ROUTE_IDS)
    info["x-cortex-ports"] = [8000, 8010]
    spec["info"] = info

    components = dict(spec.get("components") or {})
    schemas = dict(components.get("schemas") or {})
    for name, body in contract_schemas.items():
        key = name if name.startswith("Contract") else f"Contract{name}"
        schemas[key] = body
    components["schemas"] = schemas
    spec["components"] = components

    return json.loads(json.dumps(spec, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    contract_version, _ = _contract_schemas()
    out = ROOT / "contract" / f"openapi-{contract_version}.json"
    spec = build_spec()
    rendered = json.dumps(spec, indent=2, sort_keys=True) + "\n"

    digest = ROOT / "contract" / f"openapi-{contract_version}.json.sha256"
    expected = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

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
        # The sidecar is what a consumer checks a vendored copy against, so a
        # missing or stale one is drift too — otherwise DMS could verify a spec
        # against a hash that was never regenerated with it.
        if not digest.is_file():
            print(f"MISSING {digest}", file=sys.stderr)
            return 1
        recorded = digest.read_text(encoding="utf-8").split()[0].strip()
        if recorded != expected:
            print(
                f"OpenAPI digest drift: {digest.name} records {recorded[:12]}…, "
                f"spec hashes to {expected[:12]}…",
                file=sys.stderr,
            )
            return 1
        print(f"OK — {out.relative_to(ROOT)} matches regeneration, digest agrees")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" is load-bearing: `expected` above hashes the in-memory string,
    # which carries LF. Without this, write_text translates to CRLF on Windows, so
    # the file never hashes to its own sidecar and every downstream verification -
    # here, check_contract_compat.py, and the DMS contract-pin job that vendors this
    # exact file - fails on Linux CI while passing on the machine that wrote it.
    out.write_text(rendered, encoding="utf-8", newline="\n")
    digest.write_text(f"{expected}  {out.name}\n", encoding="utf-8", newline="\n")
    print(f"Wrote {out.relative_to(ROOT)} and {digest.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
