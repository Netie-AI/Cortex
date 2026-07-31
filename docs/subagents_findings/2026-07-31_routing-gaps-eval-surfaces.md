```yaml
keywords: [routing, malay, delayed_count, capacity, audit, spend, E4, envelope, ontology, trust, eval, phase1a, forecast, drillthrough, handoff]
main_idea: Live DMS routing gaps and E4 orphan-numbers fixed; Claude Ontology/Trust surfaces approved; claim stays unsupported at N=47/310; next is restart :8010, I1 importlinter decision, Phase 1b.
models: [grok-4.5, opus, fable]
workflow: orchestrate-subagents
reuse: golden_rule
status: verified
cite: task:dbce683d-c4aa-45d7-b4ec-ab576808da1f agent:3383de35-7614-4bc1-9a55-3bf8f8d2155b agent:54cf0053-f601-4c0d-9798-2b044c8b8d4e
repo: multi
date: 2026-07-31
```

# Routing gaps + Ontology/Trust review (2026-07-31)

## Main idea

- L1 router missed Malay / almost-full / audit / spend / delayed-count; fixed via vocabulary + `route_to_metric` + `delayed_count` metric.
- E4 failures were DMS envelope harvesting only the first numeric — fixed multi-row `values[]`.
- Forecast must abstain before query-skill / demo revenue paths (Cortex + DMS demo).
- Claude’s Ontology + Eval + Trust UI is ship-quality; claim honesty (47/310) correct.
- Main `:8010` must restart to load `/dms/ontology` and `/dms/eval/*`.

## Golden rule (smaller agent)

> Before researching DMS “abstain wrongly” bugs: check `packs/dms/semantic/vocabulary.py` + `answer_engine.route_to_metric` first; metrics.yaml synonyms alone do not route. For envelope 503s with `E4: orphan number`, fix values harvest in `dms_executor/envelope.py`, not the metric SQL. For “0 confidently wrong” product claims, read `/dms/eval/summary` claim blockers — never soften in UI.

## Verify

```bash
cd D:\Cortex
set PYTHONPATH=D:\Cortex
set PACK=dms
set DMS_READ_ONLY_QUERIES=1
python -m bench.corpus
python -m bench.live_probe
python -m pytest tests/dms/test_ontology_eval_routes.py tests/dms/test_vocabulary_normalization.py -q

cd D:\DMS
python -m pytest tests/test_demo_ask.py::test_forecast_never_answers_with_historical_total tests/invariants/test_boundaries.py::test_mutation_routes_call_compliance_gate -q
```

## Questions left open

- I1: C7-full `answer_engine → packs.generative` importlinter (`INVARIANT-CHANGE`)
- Phase 1b paraphrase to N=310 with `gold_verified`
- Restart `:8010` so Ontology/Eval serve on main stack

## Handoff

`docs/dms/packets/CLAUDE_CODE_HANDOFF_NEXT.md` (prompts A–H)

## Promote

- [x] docs/subagents_findings
- [x] ~/.claude + ~/.cursor protocol skills
- [ ] skill_distill capture (optional distill-session)
