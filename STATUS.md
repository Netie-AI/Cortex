# STATUS.md
**Last updated:** 2026-07-23 | **Gate:** O7 new-pack generator **PASS** | **Active:** capability landings next gate (L0 DuckLake); O6 stays owner-gated
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

> **2026-07-23 (O7 + P16 hooks + AirGPT commit):** O7 — `scripts/new_pack.py` scaffolds a full pack
> (ontology trio → generated DDL + `semantic_layer.yaml` + compliance stub + audit that reuses the
> F1 ledger) from a governed trio; demo `packs/crm/` (Account/Contact/Opportunity) shipped as the
> worked example. "Companies make the app they want." P16 — `agent_sdk/hooks.py` lifecycle hooks
> (before/after/on_denied, error-swallowing) + built-in output-secrets scanner. AirGPT initial
> commit landed (`D:\AirGPT` `b0723db`, 314 files, local-only, secrets excluded); registry paths
> F:→D: fixed. 343 pass (2 unrelated failures are the parallel track's untracked engine-autostart
> test — missing `pwsh`).

> **2026-07-23 (O3+O4+O5 + evals):** O3 — F8 allowlist resolves from `ontology_action_types`
> (`allowed_action_tools`; adding a tool = registering an action type). O4 — pack-agnostic
> **Agent SDK** at `CortexOS/agent_sdk` (`query_objects` PII-scoped reads, `call_action`
> registry→RBAC→confirm→F8→ledger; registry moved to engine home `CortexOS/ontology`, DMS shim
> kept; synthetic non-DMS pack proves pack-agnosticism). O5 — sidecar bridge
> `/dms/sidecar/{query-objects,call-action}` + AirGPT `dms_query`/`dms_action` tools, proven
> live E2E (PII-clean read, confirm_required, rbac + unregistered denials, ledgered). P16 slice:
> `agent_sdk/evals.py` scope-containment harness (**required gate before O6**). Secrets: Cortex
> `.gitignore` now excludes `CortexOS/AirGPT` mirror (live `env.local` keys); AirGPT registry
> paths F:→D: fixed. ⚠ `D:\AirGPT` repo has zero commits — initial commit pending owner.

> **2026-07-23 (final goal set):** North-star sharpened — Cortex = **the best engine** (orchestration +
> engine capability only; verticals are consumers). Two consumption modes (hosted API / downloadable
> self-host netie engine) + API docs + whitepaper. See `docs/strategy/CORTEX_FINAL_GOAL.md`, PARKING_LOT P17/P18.
>
> **2026-07-23 (context engineering):** `CortexOS/context_engineering/` + `/api/context/*`;
> ponytail + optimizer `context_engineering`. OpenIDE/AirGPT mirror is out-of-repo
> (`AirGPT/context_engineering`, `OpenIDE/docs/PROMPT_LAYERS.md`) — additive, not an O-gate.
>
> **2026-07-23 (O2):** Codebase knowledge map — `scripts/build_codebase_ontology.py` (ast,
> zero LLM) → `data/codebase_ontology.db`; `packs/dms/ontology/query.py` CLI
> (`--covers` / `--gate` / `--module`). 8/8 `test_o2_codebase_map` + secrets clean.
>
> **2026-07-23 (O1):** Ontology registry shipped — `packs/dms/ontology/{object_types,link_types,
> action_types,functions}.yaml` (transcribes `semantic_layer.yaml` + primary keys + `agent_visible`
> folding `sensitive_columns`; registers 24 ledger event literals + `export_pptx` tool) and
> `registry.py` compiling `ontology_*` tables into the F1 ops DB. 10 parity/idempotency tests lock
> it to `semantic_layer.yaml` and the F8 allowlist. Dual-brain decision: Option B via C
> (`docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md` §2); branches consolidated to
> `main` + `netie-engine`.
>
> **2026-07-22 (CI green):** Fixed Poetry `postgres` extra (`psycopg[binary]` invalid in
> `tool.poetry.extras`), added `pytz` for DuckLake, opaque `ghs_` scanner (May 2026 token
> changelog), and RLS proof as NOSUPERUSER `dms_rls_app` (superuser bypasses FORCE RLS).
> `Test` + `Secrets Scan` + `RLS Proof` green on `main` / `dms-integrated-engine`.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| V0–V1, F1–F6 | Shipped | PASS |
| F7 remainder | RLS CI + secrets + RBAC | **PASS** (CI green) |
| F8 tool-call (`export_pptx`) | Vertical slice shipped | Demo-safe |
| S1 agents + durable resume | Ops-DB checkpoints + optional DBOS | Core+B1 |
| Q1/Q2/L0–L2/S0 | Shipped | BUILD_PLAN_V2 |
| O1 ontology registry | YAML → `ontology_*` in ops DB | **PASS** |
| O2 codebase knowledge map | ast index + `--covers` / `--gate` CLI | **PASS** |
| O3 action-type registry (F8) | Allowlist from `ontology_action_types` | **PASS** |
| O4 Agent SDK | `CortexOS/agent_sdk` — pack-agnostic reads/actions | **PASS** |
| O5 sidecar-SDK bridge | `/dms/sidecar/*` + AirGPT `dms_query`/`dms_action` | **PASS** |
| O7 new-pack generator | `scripts/new_pack.py` + demo `packs/crm/` | **PASS** |
| P16 evals + hooks | scope-containment + lifecycle/output-secrets hooks | Shipped |
| Context engineering | Layered assemble + compaction + NOTES API | Shipped (additive) |

## Test baseline
```
pytest -q  (expect ≥330; local RLS skips without DSN)
python -m scripts.secrets_scan  → 0 findings
CI: Test + Secrets Scan + RLS Proof → success
```

## Next three moves
1. O7 new-pack generator (FDE payoff) + P16 hardening slices (`FABLE5_HANDOFF_PROMPTS.md` Prompts 8–9); O6 builder stays owner-gated
2. `@agent` chat dispatch (last S1 skip)
3. ~~Architecture-preset MoE router + OpenVault manager/shipper~~ — **started**: `PRODUCT_ROLES.md` + `architecture_presets` + `openvault_gate` client; OpenVault owns `/api/gate/check` + `/api/keyvault/*`

## Handoff
- **Final goal (north-star): `docs/strategy/CORTEX_FINAL_GOAL.md`**
- Truth map: `docs/dms/TRUTH_GROUND_MAP.md`
- Hand-back: `docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md`
- Research: `docs/research/findings/P0_INDEX.md`
- Context engineering: `docs/CONTEXT_ENGINEERING.md`
