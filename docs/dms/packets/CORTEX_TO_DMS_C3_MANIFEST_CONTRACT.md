# Cortex → DMS — what T2 must do to make a manifest verify

**Date:** 2026-07-30
**From:** Cortex engine lane (C3 landed)
**To:** DMS lane (T2 — manifest minting)
**Status:** binding. A manifest that deviates from any of this is refused at the executor.

C3 shipped verification and enforcement. DMS mints, Cortex enforces, OpenVault roots. This
packet is the half DMS has to match — none of it is discoverable from the OpenAPI spec,
because it is about bytes and semantics rather than shapes.

---

## 1. Contract version

Contract is **1.1.0**. `Manifest` gained `pool_id`, `issued_at`, `issuer_key_id` and
`row_predicates`, all optional on the wire so a 1.0.0 producer still validates. Your major-1
pin holds; regenerate `packages/cortex_client` from `contract/openapi-1.1.0.json`.

Optional on the wire does **not** mean optional in practice. `ManifestVerifier` refuses a
manifest with no `issuer_key_id`, no `issued_at` or no signature. The type is permissive so the
bump stayed a minor; the gate is strict so the refusal is auditable.

`row_predicate_sql` is deprecated and removed in 2.0.0. One string cannot say which table it
applies to, so it cannot be injected per table. Send `row_predicates`.

---

## 2. The bytes you sign

Call `cortex_contract.execution.canonical_manifest_bytes()` if you can. If you reimplement it,
the rule is:

1. Render the manifest as a JSON object.
2. Remove the `signature` key.
3. Recursively remove any key whose value is null, an empty array, or an empty object. **Empty
   strings are kept** — an empty string is a value.
4. Sort object keys by Unicode code point, at every level.
5. Serialise with no whitespace: `,` and `:` separators, no trailing newline, non-ASCII literal.
6. Encode UTF-8, no BOM.

Step 3 is what lets contract minors stay compatible: without it, adding an optional field would
change the bytes a newer verifier canonicalises and invalidate every signature an older signer
ever produced.

**Signature encoding: unpadded base64url of the raw 64-byte Ed25519 signature.** Not hex, not
standard base64, no JWS envelope. One encoding, strictly — a signature that is not base64url is
refused as malformed rather than tried another way.

**Timestamps: ISO-8601 with an explicit offset.** A naive timestamp is refused, not assumed
UTC. Guessing a zone means the same manifest is valid on one host and expired on another. Clock
skew tolerance is 120s in both directions.

---

## 3. Getting a signing key

OpenVault, loopback only:

```
POST /keys/services      {"service_id": "dms"}      + header X-OpenVault-Reveal: intentional
  -> {"token": "..."}    returned once; only its SHA-256 is stored
POST /keys/intermediate  {"service_id": "dms", "subject": "dms-manifest-signer", "ttl_s": 900}
  + header Authorization: Bearer <token>
  -> {"kid", "private_key", "public_key", "not_before", "not_after", "chain_signature"}
```

`private_key` is unpadded base64url of the raw 32-byte Ed25519 seed. It is returned **once** and
is not stored by OpenVault — there is no second call that will give it to you again. Hold it in
memory only, per T2's own rule: never on disk, never in a log, never in an error message.

Put the returned `kid` in the manifest's `issuer_key_id`. Cortex resolves it from
`GET /keys/jwks`, which it caches to disk and reads without touching the network on the hot
path. Maximum TTL is 3600s; 900s is the default and is the right order of magnitude. When the
key expires, ask for another — do not extend the lifetime of manifests to match.

---

## 4. What `allowed_paths` means

Globs, matched after normalisation, where **`*` does not cross a `/`**. `/pool/a/*.parquet`
grants that one directory, not the tree beneath it. Use `**` when you mean the tree.

Refused before matching, so do not bother sending them: anything with a scheme (`s3://`,
`http://`, `md:`), UNC paths, and anything that normalises to a leading `..`.

Cortex checks paths reached three ways: file-reading functions anywhere in the query
(`read_parquet`, `read_csv`, `read_json`, `parquet_scan`, and the rest), a bare quoted path in
`FROM`, and `ATTACH` targets. `ATTACH` is refused outright — a manifest grants paths to read,
not databases to mount.

---

## 5. What `row_predicates` means — read this one

`row_predicates` is **not only a filter. It is the table allowlist.**

`allowed_paths` bounds what may be read out of files. Nothing bounded what may be read out of
the database itself, so `SELECT id FROM orders UNION ALL SELECT id FROM secrets` passed every
path check. The keys of `row_predicates` are therefore the set of tables the session may name at
all, and anything else is refused.

**A table that needs no row filter must still be granted, with a predicate of `TRUE`.** If you
omit it, queries touching that table fail with `path_not_allowed`.

---

## 6. The invariant only you can hold

**Never grant both a raw path and a row predicate over the same underlying data.**

If `allowed_paths` contains the parquet files that back `orders`, a session can read them
directly and get every row the `orders` predicate exists to hide. Cortex cannot catch this: the
path is inside the manifest and no governed name is rebound, so nothing in the query is out of
bounds. Only the minting side knows which files back which table.

Grant the pre-filtered path, **or** grant the table with its predicate. Never both for the same
bytes.

This is recorded as a case in `tests/test_execution/hostile_sql_corpus.json` with
`expected: minting_invariant`, and asserted there, so nobody later reads that corpus as proof
Cortex closes it.

---

## 7. Error classes to branch on

Every refusal is a distinct exception with a stable `code`. Map them, do not parse prose:

| code | meaning | what DMS should do |
|---|---|---|
| `manifest_expired` | past `expires_at` | re-mint **once**, then fail |
| `manifest_not_yet_valid` | `issued_at` ahead of the engine's clock | fix clock sync; do not retry blindly |
| `manifest_malformed` | no issuer, bad timestamp, bad signature encoding | bug in minting; fix, never retry |
| `manifest_unknown_issuer` | `kid` not in the cached JWKS, or key expired | get a fresh intermediate |
| `manifest_signature_invalid` | signature does not verify | **security event.** Never re-mint |
| `path_not_allowed` | reaches outside the manifest | **security event.** Never re-mint |
| `statement_not_allowed` | not a read | security event |
| `sql_not_analyzable` | Cortex could not fully analyse it | surface as "unsupported query" |

`sql_not_analyzable` is a refusal, not a lesser failure. A query the enforcer cannot read is a
query it cannot prove safe.

---

## 8. Known gaps, stated rather than buried

- **Enforcement is not yet wired into the query path.** `enforce_manifest` is implemented and
  tested but no call site invokes it — that is C4's `submit()` seam, which routes every
  execution through one place. Until C4 lands, this module is a library, not a control.
- **`_true_count` in `answer_engine.py:268` executes a second query outside the guardrail.** It
  re-derives from `safe_sql`, so a predicate baked into the SQL survives — but any future
  enforcement that wraps the *connection* rather than the *SQL* would miss it. C4 should route
  it through `submit()` too.
- **`agent_sdk/backends.py` and `api/brain_routes.py` open DuckDB directly**, bypassing the
  guardrail entirely. C4's AST invariant test (no `duckdb` symbol outside
  `CortexOS/execution/`) is what closes this.
- **PIVOT and three-part catalog names are refused**, not enforced. Both are fail-closed
  choices; if a customer needs them, they are work, not config.
