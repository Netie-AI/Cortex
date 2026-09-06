---
keywords: [warehouse, pr-150, pr-149, ns_null_expiry_count, rag-03, crew-cmd, dependabot, dms-ui, c7-heldout, rsf, pr-128]
main_idea: "Warehouse #150 and #149 on main. C7-05/06 stay gated (G-sh 0, cutover false, draft #128). RSF #152 do-not-seat. Heartbeat 974ad3f0: no new PRs."
models: [grok-4.6]
workflow: cortex-ticket-follow-up
reuse: golden_rule
status: verified
cite: agent: aa157b17-8405-4f71-9a79-6384018e16dc
repo: Cortex
date: 2026-09-04
---

# Cortex ticket follow-up (2026-09-04)

PREFLIGHT: HIT
reuse: `2026-09-04_leftover-worktree-branches-close.md`
spawn: skip

## Landed

- PR #150 squash `82f9bd9`. `warehouse_path()` re-reads `DMS_WAREHOUSE_DB` at call time. Mixed #43 closed superseded.
- PR #149 squash `4b670b7`. `ns_null_expiry_count` was confidently wrong as `sku_count`. L1 now excludes `expiry`; seed abstains. Corpus gates 2 passed. Do not regenerate baseline to hide a wrong number.
- Issue #32 RAG-03 closed (PR 118 on main). Issue #130 CREW-CMD closed (PR 139).

## Left open

- #104 C7-05: scorer + L2 SQL landed; serve cutover did not. Last held-out: G-err false, **10/28 incorrect**, G-sh 0, `cutover=False`. Do not invent-green.
- #105 C7-06 / draft PR #128: lint green, CLEAN vs main, gate not met (G-err does not beat L1). Keep draft.
- #116 EPIC-CREW-01: chrome + slash on crew; `facts.md` / `memory.py` not on origin/main.
- EPIC-RSF #151 + #152-#156: do not seat until Platform slot (#152). No analog-reuse swarm.
- GOLD #13/#18, RAG-02 #33, EPIC-015 #34 PARTIAL, EPIC-001 #11, EPIC-006 #17.

## Dependabot

Closed PRs 77-81 (`demo/dms-ui`) as leftover second SPA. Do not merge red CI.

## Heartbeat (agent 974ad3f0)

No Cortex PRs opened or merged. `#149` already on main. Draft `#128` CI-green, stays draft (auto-merge skipped). GATE PASS after unpinning closed `#43`.

Do not seat:

- RSF `#151`-`#156` -- every body `DO NOT SEAT until Platform slot`. `#152` missing analog-reuse line (back to PRD). Analog already on disk: `architecture_presets.py` + `test_compile_rejects_unknown_kind`. Do not clone n8n. Do not touch P1.
- C7-05 `#104` -- G-sh last **0** (need >= 500). `score_engine` forces `cutover=false`. `#128` rebase after `#149` is L1 1 correct / 27 abstained / 0 incorrect (abstention-green, not L2 serve). No `--enable-l2` FreeRoute report. Do not start a second writer on `#128` files.
- C7-06 `#105` -- blocked-by `#104`. Do not undraft `#128`.

Warehouse leftover: `#43` CLOSED unmerged; call-time path is `#150` on main. Do not reattach `cursor/warehouse-path-4abf`.

Parked still: GOLD `#13`/`#18`, RAG-02 `#33`, EPIC-015 `#34`, EPIC-001 `#11`, EPIC-CREW-01 `#116`.

## Verify

```
gh pr view 150,149,128 -R Netie-AI/Cortex --json state,isDraft,mergeCommit
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_execution/test_warehouse_path.py tests/dms/test_corpus_seeds.py::test_corpus_confidently_wrong_zero -q
```
