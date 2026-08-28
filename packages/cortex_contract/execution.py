from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, Field


class PoolSpec(BaseModel):
    id: str
    class_name: str = "default"
    max_concurrency: int = 1


class Manifest(BaseModel):
    """A signed grant of what one session may read.

    DMS mints and signs; Cortex enforces; OpenVault roots the key. Every field
    added in 1.1.0 is optional on the wire so a 1.0.0 producer still validates —
    the requirement that a manifest carry an issuer, an issue time and a pool is
    enforced by the verifier (``CortexOS.execution.manifest``), which rejects
    what it cannot check. A permissive type with a strict gate keeps DMS pinned
    to contract major 1 while the engine refuses anything unverifiable.
    """

    session_id: str
    org_id: str
    space_id: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    expires_at: str
    signature: str

    # ── 1.1.0 ────────────────────────────────────────────────────────────────
    pool_id: str | None = None
    issued_at: str | None = None
    issuer_key_id: str | None = None
    row_predicates: dict[str, str] = Field(
        default_factory=dict,
        description="Table name -> SQL boolean expression, injected per referenced table.",
    )

    # Deprecated in 1.1.0, removed in 2.0.0: one predicate string cannot say
    # which table it applies to, so it cannot be injected per-table. Producers
    # should send row_predicates; consumers should read row_predicates first and
    # fall back to this only for manifests minted by a 1.0.0 client.
    row_predicate_sql: str | None = None


def _prune(value: Any) -> Any:
    """Drop absent values so a 1.0.0 signer and a 1.1.0 verifier agree byte for byte.

    Without this, adding an optional field to Manifest would change the bytes a
    newer verifier canonicalises and invalidate every signature produced by an
    older signer — a minor bump that breaks the wire is not a minor bump.
    """
    if isinstance(value, Mapping):
        pruned = {k: _prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def canonical_manifest_bytes(manifest: Manifest | Mapping[str, Any]) -> bytes:
    """Exact bytes both sides sign and verify. DMS mints, Cortex enforces.

    The rule, stated so a non-Python signer can reimplement it:

    1. Render the manifest as a JSON object.
    2. Remove the ``signature`` key.
    3. Recursively remove any key whose value is null, an empty array, or an
       empty object. Empty strings are kept — an empty string is a value.
    4. Sort object keys by Unicode code point at every level.
    5. Serialise with no whitespace: ``,`` and ``:`` separators, no trailing
       newline, non-ASCII left literal.
    6. Encode UTF-8, no BOM.

    Any deviation produces a different digest and the signature is refused, so
    this function is the contract — not a description of one.
    """
    if isinstance(manifest, Manifest):
        payload: Any = manifest.model_dump(mode="json")
    else:
        payload = dict(manifest)
    payload.pop("signature", None)
    return json.dumps(
        _prune(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class SubmitRequest(BaseModel):
    pool: PoolSpec
    plan: dict[str, Any]
    body: dict[str, Any]
    manifest: Manifest


class QueryResult(BaseModel):
    ok: bool
    status: str
    run_id: str | None = None
    output: Any = None
    error: str | None = None


class EngineSubmitter(Protocol):
    async def submit(
        self,
        plan: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        caller: Any = None,
    ) -> dict[str, Any]: ...
