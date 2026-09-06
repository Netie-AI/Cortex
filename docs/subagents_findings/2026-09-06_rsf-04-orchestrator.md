# RSF-04: orchestrator certifies each stage

- Date: 2026-09-06
- Keywords: rsf, rsf-04, orchestrator, certified, abstain, refuse, #154, #151
- Main idea: `CortexOS.execution.rsf_orchestrator.run_rsf` is an in-process meta-router. It emits RSF-02 wire artifacts per stage. Later CERTIFIED is illegal unless every prior `RSF_STAGES` step is CERTIFIED. RSF-01 options stay distill-only.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `CortexOS/rsf.py` (RSF-02), `CortexOS/execution/distill_options.py` (RSF-01), `CortexOS/execution/rsf_boundary.py` (RSF-03), DMS `packages/core/dms_core/rsf.py` (`RSF_STAGES` + `require_certified_priors`), Constructor `parseRsfTrace`. spawn: skip (executor).

## Golden rules

1. Pipeline order is RSF-02 `RSF_STAGES` = research → segment → classify → filter. Ticket #154 names the four stages; "prior" matches DMS/Constructor so a CERTIFIED segment cannot stand on missing research.
2. No proposal / missing evidence → ABSTAIN, not invent-green CERTIFIED. Downstream stages are not proposed once blocked.
3. Research always asks OpenVault leave-machine (`gate_research_egress`). Unreachable OV → REFUSE research, ABSTAIN the rest.
4. n8n / langchain / langflow as `chosen_option` → REFUSE. `gencfsm_dag` is learn via existing gen_cfsm → dag_runner.
5. Bake-off hooks for dms_rag / normal_chat / agentic_actions exist with null latency/cost/tokens. No target numbers. No analog execution.

## Verify

```
python -m pytest tests/test_rsf_orchestrator.py tests/test_rsf_consumer.py tests/contract/test_constructor_engine_ban.py tests/contract/test_rsf_boundary_ban.py tests/dms/test_rsf_boundary.py tests/dms/test_distill_options.py -q
```

Does not prove RSF-06 distill harness, RSF-07 corpus, Constructor HTTP mount of `body.rsf`, or GHA.
