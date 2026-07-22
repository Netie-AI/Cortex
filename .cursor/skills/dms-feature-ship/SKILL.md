---
name: dms-feature-ship
description: Ships one DMS feature (F1-F7 or V0-V3) from docs/dms/BUILD_PLAN.md or VISION_GOVERNANCE.md. Use when user says ship feature, implement F1, build V0 warehouse, or execute next build step sequentially.
---

# DMS Feature Ship

## Before coding

1. Read `STATUS.md`, `CONTEXT.md`, and the feature section in `docs/dms/BUILD_PLAN.md` or `docs/dms/VISION_GOVERNANCE.md`.
2. Read `.cursor/rules/cortex-core.mdc` and file-scoped rules for touched paths.
3. Confirm prior features in sequence are done (check `CHANGELOG_DMS.md`).

## Execute

1. **Plan** — list files to create/edit; STOP if user asked for plan review.
2. **Implement** — smallest diff; obey ANTI-SCOPE block verbatim.
3. **Test** — feature smoke tests + `python -m pytest tests/ -q`.
4. **Log** — append to `CHANGELOG_DMS.md`
5. **Handoff** — update `STATUS.md`, run `python scripts/handoff.py --write`
6. **Ponytail** — review diff for over-engineering (see `docs/PONYTAIL.md`)

## Hard stops

- Do not parallelize dependent features.
- Do not modify `packs/ruma/` or unrelated packs.
- Do not weaken existing tests.
- Do not call T3/BIG_API in classification/suggestion/PII paths.

## Demo check (optional)

After API changes:
```powershell
pip install -e ".[dev,api,dms]"
$env:PACK="dms"; python -m uvicorn CortexOS.api.main:app --port 8000
```
Or full demo: `.\demo\run_demo.ps1`
