---
keywords: [constructor, ghost, honesty, 404, n8n, tas-control, paperclip, ancestry]
main_idea: Ghost dry-run now has honesty tests (no run_dag, 401 without key, 404 is not live). TAS-CONTROL names Python :8040, not a Paperclip fork. Goal open. Founder cards unchanged.
models: [cursor-grok-4.6]
workflow: 2026-08-28_constructor-ghost-tas-honesty
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#2
repo: Cortex
date: 2026-08-28
---

# Constructor ghost honesty + TAS-CONTROL ancestry (2026-08-28)

PREFLIGHT: HIT - ghost compile existed; 404 honesty test did not. Activity governance already shipped 2026-08-27. Pointer/Space/Control belt/Crew host not redone.

## Main idea

`POST /cortex/constructor/ghost` compiles only. Skin `engine.js` already returned `{ok:false, status}` on HTTP error; README still listed `app.netie.ai/cortex` as a live engine. README now says 404 until Hyperlift. TAS-CONTROL header and surfaces now match `E:\NetieControl` Python, not `paperclip/server`.

## Golden rule

> Ghost is compile. 404 is blocked, not live. Control is Python, not a Paperclip fork.

## Already there (not this ship)

- `test_ghost_compiles_with_viewer_key`
- Activity `governance` tests (3 passed this run)
- engine.js `cortexPost` !res.ok -> status; ghostRun only "compile ok" when remote.ok
- Pointer `cursor/recall-hud-default-off` ac4b3ea; Space `cursor/space-ci-honest` 37b77ef

## New

- `test_ghost_401_without_key`
- `test_ghost_does_not_call_run_dag_or_fetch`
- `test_skin_ghost_honesty_does_not_claim_live_on_404`
- `CortexOS/constructor_skin/README.md` + `E:\constructor\README.md` 404 line
- `E:\Netie\TAS\TAS-CONTROL.md` ancestry
- `E:\NetieControl\tests\test_control_stays_plane_4.py::test_shipped_control_is_python_not_a_paperclip_fork`

## Do not

- dms#61, work.netie.ai, HT1-HT5, grok-bot copy, n8n clone, kill :8765 / :8010
