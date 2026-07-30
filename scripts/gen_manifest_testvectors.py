#!/usr/bin/env python3
"""Emit ``contract/testvectors/manifest_canonical.jsonl``.

DMS signs the bytes ``canonical_manifest_bytes()`` produces and Cortex verifies
them. If the two sides ever disagree by one byte, every manifest DMS signs fails
verification — and it presents as a signature bug, not a serialisation bug, which
is the worst possible place to spend a debugging session.

So the rule ships as data, not prose. Each line is one manifest plus the SHA-256
of its canonical bytes. Any implementation in any language can assert against
this file and prove agreement without reimplementing the rule from a document.

The vectors deliberately cover the places a reimplementation goes wrong:

* absent vs empty vs present-but-falsy — the pruning rule
* key ordering, including inputs whose insertion order differs from sorted order
* non-ASCII and astral-plane characters in paths, which ``ensure_ascii`` would mangle
* a 1.0.0-shaped manifest, which must hash the same as its 1.1.0 equivalent
* timestamp spellings, offsets, and boundary values
* an empty string, which is kept, next to an empty collection, which is dropped
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contract" / "testvectors" / "manifest_canonical.jsonl"

sys.path.insert(0, str(ROOT))

from packages.cortex_contract.execution import (  # noqa: E402
    Manifest,
    canonical_manifest_bytes,
)

BASE: dict[str, Any] = {
    "session_id": "sess-1",
    "org_id": "acme",
    "expires_at": "2030-01-01T00:00:00+00:00",
    "signature": "REPLACED-AND-EXCLUDED",
}


def _vector(name: str, why: str, **overrides: Any) -> dict[str, Any]:
    payload = {**BASE, **overrides}
    manifest = Manifest(**payload)
    canonical = canonical_manifest_bytes(manifest)
    return {
        "name": name,
        "why": why,
        "manifest": manifest.model_dump(mode="json"),
        "canonical_utf8": canonical.decode("utf-8"),
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "canonical_length": len(canonical),
    }


def vectors() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    add = out.append

    add(_vector("minimal", "Only the fields required in 1.0.0."))
    add(
        _vector(
            "one_path_one_predicate",
            "The ordinary case.",
            allowed_paths=["/data/pool/acme/*.parquet"],
            row_predicates={"orders": "tenant_id = 'acme'"},
        )
    )
    add(
        _vector(
            "empty_predicates_dropped",
            "An empty map is removed, so this must hash identically to `minimal`.",
            row_predicates={},
        )
    )
    add(
        _vector(
            "empty_paths_dropped",
            "An empty list is removed. Same digest as `minimal`.",
            allowed_paths=[],
        )
    )
    add(
        _vector(
            "nulls_dropped",
            "Explicit nulls are removed, not serialised. Same digest as `minimal`.",
            pool_id=None,
            issued_at=None,
            issuer_key_id=None,
            space_id=None,
        )
    )
    add(
        _vector(
            "empty_string_kept",
            "An empty string is a value and is KEPT — the one exception to pruning. "
            "A reimplementation that drops falsy values instead of absent ones fails here.",
            space_id="",
        )
    )
    add(
        _vector(
            "full_1_1_0",
            "Every 1.1.0 field populated.",
            space_id="space-7",
            pool_id="pool-a",
            issued_at="2026-07-30T09:15:00+00:00",
            issuer_key_id="int-abc123",
            allowed_paths=["/data/pool/acme/*.parquet", "/data/pool/shared/ref.parquet"],
            row_predicates={"orders": "tenant_id = 'acme'", "users": "tenant_id = 'acme'"},
        )
    )
    add(
        _vector(
            "predicate_key_order_reversed",
            "Predicate keys given in reverse order must hash the same as `full_1_1_0` "
            "would with them forward — sorting is recursive, not top-level only.",
            space_id="space-7",
            pool_id="pool-a",
            issued_at="2026-07-30T09:15:00+00:00",
            issuer_key_id="int-abc123",
            allowed_paths=["/data/pool/acme/*.parquet", "/data/pool/shared/ref.parquet"],
            row_predicates={"users": "tenant_id = 'acme'", "orders": "tenant_id = 'acme'"},
        )
    )
    add(
        _vector(
            "path_order_is_significant",
            "allowed_paths is a LIST: order is preserved, not sorted. This must NOT "
            "hash the same as `full_1_1_0`.",
            space_id="space-7",
            pool_id="pool-a",
            issued_at="2026-07-30T09:15:00+00:00",
            issuer_key_id="int-abc123",
            allowed_paths=["/data/pool/shared/ref.parquet", "/data/pool/acme/*.parquet"],
            row_predicates={"orders": "tenant_id = 'acme'", "users": "tenant_id = 'acme'"},
        )
    )
    add(
        _vector(
            "unicode_path_latin",
            "Non-ASCII stays literal UTF-8; ensure_ascii would escape it and change the bytes.",
            allowed_paths=["/data/pool/café/ventes-2026.parquet"],
        )
    )
    add(
        _vector(
            "unicode_path_cjk",
            "Multi-byte CJK in a path.",
            allowed_paths=["/data/pool/仓库/库存.parquet"],
        )
    )
    add(
        _vector(
            "unicode_path_astral",
            "Astral-plane characters — a surrogate-pair bug shows up here, not earlier.",
            allowed_paths=["/data/pool/\U0001f600/emoji.parquet"],
        )
    )
    add(
        _vector(
            "unicode_predicate",
            "Non-ASCII inside a predicate expression.",
            row_predicates={"orders": "region = 'Zürich'"},
        )
    )
    add(
        _vector(
            "predicate_with_quotes_and_escapes",
            "Quoting inside a predicate must survive JSON escaping unchanged.",
            row_predicates={"orders": "name = 'O''Brien' AND note <> \"x\\y\""},
        )
    )
    add(
        _vector(
            "predicate_key_unicode",
            "A non-ASCII table name — sorting is by code point, not locale.",
            row_predicates={"orders": "a = 1", "Ünter": "b = 2", "zebra": "c = 3"},
        )
    )
    add(
        _vector(
            "timestamp_utc_offset",
            "Explicit +00:00 offset, the form the verifier prefers.",
            issued_at="2026-07-30T09:15:00+00:00",
            expires_at="2026-07-30T09:30:00+00:00",
        )
    )
    add(
        _vector(
            "timestamp_zulu",
            "Z suffix. Canonicalisation does NOT normalise it — the string is signed as "
            "written, so Z and +00:00 give different digests even though the verifier "
            "treats both as the same instant.",
            issued_at="2026-07-30T09:15:00Z",
            expires_at="2026-07-30T09:30:00Z",
        )
    )
    add(
        _vector(
            "timestamp_non_utc_offset",
            "A non-UTC offset is preserved verbatim.",
            issued_at="2026-07-30T17:15:00+08:00",
            expires_at="2026-07-30T17:30:00+08:00",
        )
    )
    add(
        _vector(
            "timestamp_microseconds",
            "Sub-second precision is part of the signed string.",
            issued_at="2026-07-30T09:15:00.123456+00:00",
            expires_at="2026-07-30T09:30:00.654321+00:00",
        )
    )
    add(
        _vector(
            "timestamp_far_future",
            "Boundary value; must not overflow any implementation's date handling.",
            expires_at="9999-12-31T23:59:59+00:00",
        )
    )
    add(
        _vector(
            "many_paths",
            "A wider list, to catch a per-element separator bug.",
            allowed_paths=[f"/data/pool/acme/part-{i:04d}.parquet" for i in range(12)],
        )
    )
    add(
        _vector(
            "many_predicates",
            "Ten tables, given unsorted.",
            row_predicates={f"t{i}": f"c{i} = {i}" for i in (9, 3, 7, 1, 5, 0, 8, 2, 6, 4)},
        )
    )
    add(
        _vector(
            "deprecated_field_present",
            "A 1.0.0 producer sending row_predicate_sql. Present, so it is signed.",
            row_predicate_sql="tenant_id = 'acme'",
        )
    )
    add(
        _vector(
            "both_predicate_forms",
            "Migration window: a producer sending old and new together.",
            row_predicate_sql="tenant_id = 'acme'",
            row_predicates={"orders": "tenant_id = 'acme'"},
        )
    )
    add(
        _vector(
            "path_with_json_significant_chars",
            "Backslashes and quotes in a path exercise JSON escaping.",
            allowed_paths=["/data/pool/acme/a\\b\"c.parquet"],
        )
    )
    return out


def main() -> int:
    rows = vectors()

    digests = {row["canonical_sha256"] for row in rows}
    # Sanity: the four "same as minimal" vectors must genuinely collide, and the
    # order-significant one must not. If pruning ever changes, this fails here
    # rather than silently emitting vectors that assert the wrong thing.
    by_name = {row["name"]: row["canonical_sha256"] for row in rows}
    same_as_minimal = [
        "empty_predicates_dropped",
        "empty_paths_dropped",
        "nulls_dropped",
    ]
    for name in same_as_minimal:
        if by_name[name] != by_name["minimal"]:
            print(f"{name} should hash identically to minimal but does not", file=sys.stderr)
            return 1
    if by_name["path_order_is_significant"] == by_name["full_1_1_0"]:
        print("allowed_paths order must be significant but is not", file=sys.stderr)
        return 1
    if by_name["empty_string_kept"] == by_name["minimal"]:
        print("an empty string must be kept, not pruned", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")

    print(f"Wrote {OUT.relative_to(ROOT)} — {len(rows)} vectors, {len(digests)} distinct digests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
