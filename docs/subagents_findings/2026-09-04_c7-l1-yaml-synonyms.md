# L1 honors metrics.yaml synonym phrases

- Date: 2026-09-04
- Keywords: metrics.yaml, synonyms, route_to_metric, sellers, cost_by_destination, C7-05, G-lat
- Main idea: After the regex cascade, L1 takes the longest `metrics.yaml` synonym (space, >=16 chars, no required slot). `selling`/`sellers` count as sales-rank. `sellers` is an inventory alias so `best sellers` does not abstain as an unknown subject. Shipment *cost* by destination is not a shipment *count*.

## PREFLIGHT

HIT. reuse: `docs/subagents_findings/2026-09-04_c7-design-and-shadow-mode.md`. spawn: skip.

## Golden rules

1. Regex cascade still wins. Synonym fallback is last, not C7-06 retirement of `route_to_metric`.
2. Do not treat one-word yaml fragments (`reorder`, `by carrier`) as a metric id.
3. G-lat (2026-09-04, 28 held-out): SHADOW-off p95 ~68ms. SHADOW-on extra is not a serve gate (~8s p95 on an 8-item L2 sample).
4. Do not set `DMS_L2_ENABLED` as the process default. Cortex `#104` stays open until SHADOW-off p95 is accepted on a merged tree and L2-on-miss is an explicit serve flip.

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_certified_synonyms.py tests/dms/test_ans02_aggregate_over_ranking.py -q
```

Does not prove: every yaml fragment (62 still include short/incomplete phrases), L2 serve, or live `POST /v1/chat/ask`.
