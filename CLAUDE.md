# Cortex - engine invariants for any agent

Cortex is **the engine**: answer plane, execution, ledger, compliance gate, semantic
layer, context assembly. DMS is a consumer app. OpenVault is key custody.

**Start at `AGENTS.md`**: index, merge flow, binding rules, lane rules and verify
commands live there once and are not repeated here. This file holds the engine
invariants. Section numbers are stable because tests cite them.

If a rule here conflicts with an instruction you were given, say so before acting.

## 1. Hard invariants - never break these

| Invariant | Why |
|---|---|
| `CortexOS/**` must never import `packs.*` | Packs register **into** the engine; the engine only holds a port. To reach a pack, declare a Protocol engine-side and have the pack register an implementation - see `CortexOS/audit/ledger_registry.py`. |
| `duckdb` appears only under `CortexOS/execution/` | One place opens the database, so one place can enforce a manifest. |
| DMS must never import `CortexOS` | DMS speaks HTTP through its generated client. |
| DMS **may and should** pin `cortex-contract` | Models and the canonicalisation rule must be *the same code*. `cortex_contract` has zero `CortexOS` imports by its own lint rule. Do not vendor or fork it. |
| One ledger | Consumers append through Cortex. Nothing else maintains a hash chain. |
| Engine and contract versions are independent | Engine (`pyproject.toml`) tracks G-gates; contract (`packages/cortex_contract/version.py`) tracks the wire. `scripts/check_versions.py` fails the build if code assumes they are equal. |

## 2. Protected paths - commit body must contain `INVARIANT-CHANGE:`

`tests/contract/**`, `tests/invariants/**`, `.importlinter`. CI fails otherwise. If
you are editing one to make a test pass, the test is the requirement.

**Do not add files to `_C2_ALLOWLIST`** in `tests/contract/test_import_boundaries.py`.
It is pre-C2 debt being evacuated; new crossings must fail immediately. It matches by
**exact repo-relative path** and may only shrink: evacuating a file deletes its line
in the same commit.

**Do not trust `lint-imports` alone on this boundary.** grimp does not record
function-level imports, and nearly every crossing here is a deferred import inside a
function (it reported C2 KEPT while `CortexOS/dms/answer_engine.py` made 18 `packs.*`
imports). The AST test is the stricter check; `.importlinter` is the second net.

## 3. `contract/` - frozen artifacts, never hand-edited, never deleted

Every published `contract/openapi-X.Y.Z.json` (+ `.sha256`) is frozen: a consumer may
be pinned to it, so add, never remove. `compat.yaml` lists the lines this engine still
serves; `testvectors/` holds canonicalisation vectors both repos assert against.
Regenerate the current spec with `python scripts/export_openapi.py` (needs `.[full]`),
never by hand; CI fails on drift. Consumers do not write to `contract/`.

## 4. `packages/cortex_contract/` - a field change is a version decision

Models and Protocols only. Zero `CortexOS` imports, ever.

| Change | Version |
|---|---|
| Add a field or endpoint a consumer can ignore | contract **minor** |
| Remove, rename, or retype a field a consumer reads; change an existing field's meaning | contract **major** - a coordinated release, not a commit |
| Fix that does not alter the wire | contract patch |

Deprecate alongside: keep the old field, add the new one, remove at a major you were
taking anyway. Bump **both** `version.py` and `pyproject.toml` (asserted equal), then
regenerate the spec in the *same commit*.

**`canonical_manifest_bytes()` is the most dangerous function in the repo.** DMS signs
those exact bytes; Cortex verifies them. Change the rule and every manifest DMS signs
stops verifying, presenting as a crypto bug. Any change is a contract major plus a
coordinated DMS release, and `contract/testvectors/manifest_canonical.jsonl` must be
regenerated and re-agreed.

## 5. `CortexOS/execution/manifest.py` is a security control

It decides what a session may read.

1. **Never weaken a refusal to make a query work.** Refusals are fail-closed on
   purpose: a query the enforcer cannot fully analyse is one it cannot prove safe. If
   real analytics is refused, narrow the check; do not remove it.
2. **Re-run the corpus after any change:** `python -m pytest tests/test_execution/ -q`
   (201 tests).

`tests/test_execution/hostile_sql_corpus.json`: cases may be **added**. Never
reclassify a case to `allow_but_predicate_must_apply` to make a test pass. The one
`minting_invariant` case documents a gap Cortex cannot close; it is asserted so nobody
reads the corpus as proof that it can.

## 6. Working alongside other lanes

See `AGENTS.md`.

## 7. Verifying, honestly

See `AGENTS.md`.

## 8. Answer-path tests assert user-visible output

Every answer-path test asserts on the **rendered answer text** and the **returned
rows**, not only on generated SQL. SQL assertions are permitted only *in addition*. A
gate that asserts an intermediate artifact certifies a broken feature (the SKU-BETA /
`NOT IN ('BETA')` false verification).

**Value normalization** - filter tokens resolve to the column's actual encoding
(`BETA` -> `SKU-BETA`, location dual-coding, case/whitespace). Prefer abstain over a
filter that matches nothing while the envelope stamps success.

**Customer envelope** - every gate also asserts on the artifact the customer receives;
Cortex-side assertions are necessary and insufficient. For DMS, assert badge /
abstained / values / sources / drillthrough_token / audit_id on the envelope from
`POST /v1/chat/ask` (DMS `assert_envelope_valid`). A green badge on abstention prose
is a P0.
