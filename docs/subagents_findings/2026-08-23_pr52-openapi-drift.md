```yaml
keywords: [pr52, ci, openapi, constructor, lint-type-test, sha256]
main_idea: "PR #52 lint-type-test failed on OpenAPI drift: Constructor Pydantic models leak into components.schemas; regenerate json plus sha256 sidecar, no contract version bump."
models: [grok-4.6]
workflow: none
reuse: golden_rule
status: raw
cite: agent: 35f2f684-2dfa-403d-abd9-2b6cd968f18f
repo: Cortex
date: 2026-08-23
```

# PR #52 OpenAPI drift (Constructor mount)

PREFLIGHT: PARTIAL
reuse: constructor-builtin-whatsapp-mcp (mount exists; CI still red)
spawn: skip

## Main idea

- Failed check was OpenAPI `--check`, not ruff/mypy/pytest.
- `packs/dms/constructor_routes.py` registers `SessionBody` / `ConstructorRunBody` / `IssueKeyBody` on the FastAPI app. `export_openapi.py` filters contract *paths* but keeps `components.schemas`.
- Smallest fix: `python scripts/export_openapi.py` (needs `.[full]`) and commit `contract/openapi-1.2.0.json` plus `contract/openapi-1.2.0.json.sha256`.
- Do not bump contract version; allowlisted operationIds did not change.

## Keywords (search)

`pr52`, `ci`, `openapi`, `constructor`, `lint-type-test`, `sha256`

## Golden rule (if reusable)

> After adding FastAPI BaseModels to the live app, regenerate both `contract/openapi-*.json` and its `.sha256` sidecar. `--check` treats a stale digest as drift even when the json was committed.

## Verify

```
python scripts/export_openapi.py --check
```
