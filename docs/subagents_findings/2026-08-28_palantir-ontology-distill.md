---
keywords: [palantir, ontology, semantica, cogitorium, parked, distill, ledger, skillcard]
main_idea: Palantir stays parked. Unify live fragments (compliance YAML, ontology action ids, semantic_layer projection, SkillCard, F1/F8 provenance). Do not clone Semantica or Cogitorium.
models: [cursor-grok-4.6]
workflow: 2026-08-28_palantir-ontology-distill
reuse: golden_rule
status: verified
cite: distill: E:\Cortex\docs\ontology\CORTEX_ONTOLOGY_PLAN.md
repo: Cortex
date: 2026-08-28
---

# Palantir ontology distill (2026-08-28)

PREFLIGHT: HIT - Netie STATUS Later + Cortex P1. Gap is join, not greenfield.

## Main idea

Parked until a paying client. Distill into existing packs. COPY none.

Analogs (signal only): Cogitorium = permission-as-edge. Semantica = decision provenance.

## 5 DISTILL ideas (unify existing)

1. One action namespace -- `packs/*/compliance/*_rules_v1.yaml` keys share ids with `ontology/action_types.yaml` and ledger `event_type`.
2. Semantic layer as projection -- `semantic_layer.yaml` for NL->SQL; `object_types`/`link_types` as the agent-facing view of the same tables. No second ontology store.
3. Governance spine, pack skin -- F1 ledger / F5 engine / F7 RBAC stay pack-agnostic; packs ship YAML only.
4. SkillCard -> action_type -- `required_tools` / network / tier map onto ontology action rows with optional `object_type` scope.
5. Provenance without a KG product -- hash-chained ledger rows keyed by ontology action ids (+ O2 codebase_ontology). Not RDF/LPG.

Cite: `docs/ontology/CORTEX_ONTOLOGY_PLAN.md` §1; `packs/dms/ontology/*`; `PARKING_LOT.md` P1.
