# RSF-07: eval corpus R-0003 + Audit/Operate trace

- Date: 2026-09-06
- Keywords: rsf, rsf-07, eval, corpus, r-0003, audit, operate, certified, abstain, refuse, #156, #151
- Main idea: Frozen RSF cases score live `run_rsf` with precision-on-answered (one WRONG CERTIFIED fails). Audit/Operate traces show per-stage status + chosen_option + route_trace and never display invented CERTIFIED after a gap.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `CortexOS/execution/rsf_orchestrator.py` (RSF-04), `CortexOS/rsf.py` (RSF-02), `CortexOS/execution/distill_harness.py` (RSF-06), Constructor AUDIT inspect. spawn: skip (executor).

## Golden rules

1. Consume `run_rsf`. Do not reimplement research->segment->classify->filter.
2. CERTIFIED with the wrong chosen_option, or CERTIFIED when gold is ABSTAIN/REFUSE, is WRONG. Suite fails on one WRONG.
3. Audit/Operate may only paint CERTIFIED when priors are CERTIFIED and the artifact parses. Gap CERTIFIED is shown as ABSTAIN.
4. Corpus includes happy / abstain / refuse, FreeRoute vs OmniRoute, and dms_rag / normal_chat / agentic_actions. Bake-off metrics stay null.
5. No `/v1/rsf`, no CCA edit, no Crew dual-seat, no n8n/LC/LF as product engine.

## Verify

```
python -m pytest tests/test_rsf_eval.py tests/test_rsf_orchestrator.py tests/test_rsf_consumer.py tests/contract/test_constructor_engine_ban.py tests/contract/test_rsf06_paste_ban.py tests/dms/test_constructor_graph.py -q
```

Does not prove Constructor HTTP mount of `body.rsf`, epic COMPLETE, or GHA. R-0003 different-run verify is this pytest, not this implementer claiming COMPLETE.
