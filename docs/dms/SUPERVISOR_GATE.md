# Claude Supervisor Gate — DMS Build

Use this when returning to Claude (or any external verifier) between milestones. **Do not start the next feature until the gate passes.**

---

## Gate 0 — Repo governance (one-time)

Paste:

1. `docs/README.md` index exists; planning docs under `docs/dms/`
2. `.cursor/rules/` + `.cursor/skills/` + `.cursor/AGENTS.md` present
3. `python -m pytest tests/test_dms/ -q` output
4. `.\demo\run_demo.ps1` health check (`/health` returns 200)

---

## Gate F1 — Audit ledger

From `docs/dms/BUILD_PLAN.md` Feature 1 acceptance + smoke tests.

Proof required:
- Hash chain append is serialized (no gap under concurrent writes)
- `CHANGELOG_DMS.md` entry

---

## Gates F2–F7

Same pattern per feature in BUILD_PLAN. Sequential: F2 → F3 → F4 → F5 → F6 → F7.

---

## Gate V0 — Warehouse spine

Paste:

1. **Shipped:** files from `CHANGELOG_DMS.md`
2. **Tests:** `pytest tests/dms/test_v0_warehouse.py -q` + full suite
3. **Governance proof:**
   - RLS on `dms_locations`, `dms_items`, `dms_movements`
   - EXIF GPS stripped on intake photos
   - Ledger entries for `item.intake` and `item.moved`
4. **Open questions** — anything uncertain before V1

---

## Gate V1 / V2 / V3

See `docs/dms/VISION_GOVERNANCE.md` §5. Each gate needs dimension-confirm, no-generation-model-in-measurement (V1), slotting constraints (V2), vision-assist-not-replacement (V3).

---

## Paste template

```markdown
## Gate: [F1 | V0 | V1 | ...]

### Shipped
[paste CHANGELOG_DMS.md section]

### Test output
```
[paste pytest output]
```

### Governance proof
- [ ] item 1
- [ ] item 2

### Uncertain / needs review
- ...
```
