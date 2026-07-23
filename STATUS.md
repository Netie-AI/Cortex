# STATUS.md
**Last updated:** 2026-07-23 | **Gate:** O1 ontology registry **PASS** | **Active:** O-series per `docs/strategy/FABLE5_HANDOFF_PROMPTS.md` (next: O2)
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

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

## Test baseline
```
pytest -q  (expect ≥283; local RLS skips without DSN)
python -m scripts.secrets_scan  → 0 findings
CI: Test + Secrets Scan + RLS Proof → success
```

## Next three moves
1. O2 codebase knowledge map (`docs/strategy/FABLE5_HANDOFF_PROMPTS.md` Prompt 2)
2. O3 action-type registry = F8 through ontology (Prompt 3)
3. `@agent` chat dispatch (last S1 skip)

## Handoff
- Truth map: `docs/dms/TRUTH_GROUND_MAP.md`
- Hand-back: `docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md`
- Research: `docs/research/findings/P0_INDEX.md`
