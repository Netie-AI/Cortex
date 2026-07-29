# Releasing Cortex

Three **independent** version lines. Never assume any two are equal.

| Line | Where | Current | Tracks |
|------|--------|---------|--------|
| **Engine** | `CortexOS.__version__`, root `pyproject.toml` | `2.5.0` | G-gates (G2.5 → 2.5.x) |
| **Contract** | `packages/cortex_contract/version.py` + `packages/cortex_contract/pyproject.toml` | `1.0.0` | DMS↔engine wire (`contract/openapi-1.0.0.json`) |
| **Git tag / Docker** | `v*` tags → `cortex:${VERSION}-core` / `-full` | cut from engine when shipping | Release artifacts |

`scripts/check_versions.py` asserts contract `version.py` matches the packaged contract version and scans for code that couples engine ↔ contract versions.

## Install profiles

```bash
pip install .                  # base — answer engine, execution primitives, ledger, F5/F7, semantic, context
pip install ".[agentic]"       # + DAG, OSR, seeker, routines, commitments
pip install ".[rag]"           # + embeddings / doc index
pip install ".[full]"          # agentic + rag
```

Missing extras raise `CortexOS.packaging.FeatureNotInstalled` naming the extra (`pip install 'netie[agentic]'` / `'netie[rag]'`).

Docker:

- `Dockerfile.core` → `cortex:${VERSION}-core` (`CORTEX_PROFILE=core`)
- `Dockerfile.full` → `cortex:${VERSION}-full` (`CORTEX_PROFILE=full`)

## Cutting a release

1. Ensure CI green on `main` (ruff, mypy, pytest, import-linter, `check_versions.py`, OpenAPI drift, base-install).
2. Bump engine and/or contract versions deliberately (they move on different clocks).
3. Regenerate the wire if contract models or routes changed:

   ```bash
   python scripts/export_openapi.py
   ```

   Commit `contract/openapi-<CONTRACT_VERSION>.json`. Drift breaks CI.
4. Tag and push:

   ```bash
   git tag -a v2.5.0 -m "release: engine 2.5.0"
   git push origin v2.5.0
   ```

5. `.github/workflows/release.yml` builds both images, the wheel, a conventional-commit changelog since the previous `v*` tag, and a GitHub Release with the wheel, OpenAPI spec, and `SHA256SUMS`.

## Release / X.Y branches

Cut a `release/X.Y` branch **only** when a customer is pinned to that line and needs a fix that `main` has already moved past.

Cherry-pick direction:

1. Land the fix on **`main` first**.
2. Cherry-pick **down** onto `release/X.Y`.
3. **Never** cherry-pick up from a release branch onto `main` — that re-introduces divergence. If `main` still needs the change, open a normal PR from a topic branch.

## Protected paths

Changes under `tests/invariants/**`, `tests/contract/**`, or `.importlinter` require `INVARIANT-CHANGE:` in the **commit body** (CI `protected-paths` job).
