# RSF-06: distill harness analog → Netie-native

- Date: 2026-09-06
- Keywords: rsf, rsf-06, distill, harness, myn8n, langchain, langflow, gencfsm, #155, #151
- Main idea: `CortexOS.execution.distill_harness.run_distill` takes an options-class recipe and emits Netie-native learn traces. improve→scale-check→improve. Paste as product_engine is REFUSE. Scale-check miss is INCOMPLETE, not invent-green COMPLETE.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `CortexOS/execution/distill_options.py` (RSF-01), `CortexOS/rsf.py` (RSF-02), `CortexOS/execution/rsf_boundary.py` (RSF-03), `CortexOS/execution/rsf_orchestrator.py` (RSF-04 bake-off surfaces + banned ids), `CortexOS/constructor_graph.py` (banned kinds). spawn: skip (executor).

## Golden rules

1. Options (myn8n / langchain / langflow / gencfsm_dag) are distill/compete/learn. Product engine stays cortex. Never import analog packages.
2. Study trees are names-only via `read_study_tree`. gencfsm_dag studies `CortexOS/execution/gen_cfsm.py` + G1 docs, then compiles through existing dag_runner.
3. Loop is improve → scale-check → improve. COMPLETE only after a passing scale-check with native evidenced candidates. Otherwise INCOMPLETE / ABSTAIN / REFUSE.
4. Scale surfaces: dms_rag, normal_chat, agentic_actions, constructor. OmniRoute :20128 is refuse; prefer FreeRoute.
5. Constructor compile still rejects analog kinds. importlinter contract 3 includes `distill_harness`.

## Verify

```
python -m pytest tests/test_distill_harness.py tests/contract/test_rsf06_paste_ban.py tests/contract/test_constructor_engine_ban.py tests/contract/test_rsf_boundary_ban.py tests/dms/test_distill_options.py tests/dms/test_rsf_boundary.py tests/test_rsf_orchestrator.py -q
```

Does not prove RSF-07 corpus, Constructor HTTP mount of `body.rsf`, or GHA.
