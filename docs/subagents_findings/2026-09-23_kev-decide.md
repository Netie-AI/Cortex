# KEV-DECIDE: calibrated noul/choice/score decision port behind JudgmentModel (opt-in)

- **Date:** 2026-09-23
- **Keywords:** kev, decision, calibration, temperature, ECE, Brier, judgment model, tier routing, order sensitivity, loopback
- **Main idea:** `CortexOS/decision/` is a kev-shaped decision port (noul / choice / score) with stdlib calibration metrics. `JudgmentModel` stays rules-v0 by default; a backend is opt-in via constructor or `CORTEX_KEV_URL` (loopback only). Rules answers are stamped `calibrated=False`; kev failures degrade loudly, never silently.
- **Verify:** `python -m pytest tests/test_decision -q -p no:cacheprovider` (26 tests) then `python -m pytest tests/ -q -p no:cacheprovider`
- **Does not prove:** any live kev server; kev's own calibration quality; any Jev integration or number (none claimed, no key); a trained tier classifier (plan §9 DistilBERT is still not done).
- **Cite:** CORTEX_COMPLETE_PLAN.md §1 gap "Model router: JM judgment model is rules-v0". kev wire format verified from jaredpalmer/kev README (request `state/model/questions`, response `answers.<id>.{noul | choice,probabilities,confidence | score,legend,probabilities,confidence}`).

## Expected vs actual

- Expected (plan): the router's tier decision is a calibrated probability with an abstain path.
- Actual before: `JudgmentModel.decide` returns hand-written constants (0.99 / 0.95 / 0.92 / 0.7) labelled `confidence`. Nothing in the repo computed softmax, temperature, Brier or ECE (grep over `CortexOS`, `packages`, `scripts` returned nothing), so nothing could be reused.
- Actual after: same default output byte-for-byte (`tests/test_execution/test_tier_routing.py`, `tests/dms/test_f7_security.py` untouched and green). With a backend, the tier is a `ChoiceQuestion` over T0..T3 answered through `decide()`.

## Repro

```
python -c "from CortexOS.routing import JudgmentModel, JudgmentRequest; print(JudgmentModel().decide(JudgmentRequest('chat','hello')))"
# JudgmentDecision(tier=T1, confidence=0.7, reason='heuristic routing fallback')   <- unchanged
CORTEX_KEV_URL=http://kev.example.com python -c "from CortexOS.execution.model_router import ModelRouter; ModelRouter()"
# ValueError: KevHttpBackend refuses non-loopback URL ...                          <- fail closed
```

## What was built

| Piece | Where |
|---|---|
| Question/answer models, 1..255 unique options, `to_kev()` | `CortexOS/decision/models.py` |
| `softmax`, `fit_temperature` (golden-section on log T minimising NLL), `brier`, `ece`, `choice_confidence` = (p_max - 1/K)/(1 - 1/K), `automatable_share` (largest confidence-ordered prefix within a 5% error budget) | `CortexOS/decision/calibration.py` |
| `DecisionBackend` Protocol, `RulesBackend` (wraps rules-v0, `calibrated=False`), `KevHttpBackend` (POST `/v1/systemone`, loopback only, HTTP/parse error -> degraded with cause) | `CortexOS/decision/backends.py` |
| `decide()`: temperature on logits, confidence, abstain below threshold (`CORTEX_DECISION_ABSTAIN_THRESHOLD`, default 0.5), reversed-order check -> `order_sensitive` abstain | `CortexOS/decision/decide.py` |
| Opt-in: `JudgmentModel(decision_backend=...)`, `JudgmentModel.from_env()`, `rules_decide()` kept as the v0 path; `ModelRouter` default uses `from_env()` | `CortexOS/routing/judgment_model.py`, `CortexOS/execution/model_router.py` |

When the backend abstains or degrades, `JudgmentModel` returns the rules tier with a reason that names the backend, the cause and "rules fallback". That is a visible fallback in the reason string, not a silent one.

## Root-cause class

Missing capability, not a bug: rules confidence was a label on a constant. Class: "intermediate artifact certified as a probability". No invariant was broken.

## Which invariant applies

- C2 boundary (`CortexOS/**` never imports `packs.*`): `CortexOS/decision` imports only stdlib, httpx and `CortexOS.routing`. `lint-imports` 3 kept; `tests/contract` 95 passed.
- Keys live in OpenVault: `KevHttpBackend` refuses any non-loopback URL at construction and sends no credentials.
- No contract change: no new endpoint, `contract/` untouched, `cortex_contract` untouched.

## Verified vs assumed

Verified:
- kev request/response JSON shape (README fetched 2026-09-23).
- Default routing output unchanged (existing tests green; see repro).
- `fit_temperature` recovers T=2.5 within |log ratio| < 0.15 on 3000 synthetic 4-way samples.
- Brier, ECE, automatable share on hand-computed cases in `tests/test_decision/test_calibration.py`.
- Full suite, ruff on new code, `lint-imports`, contract tests (numbers in the commit report).

Assumed:
- kev's response probabilities are already temperature-scaled by the server (README says per-model temperature fitted on dev data). `KevHttpBackend(server_calibrated=True)` stamps that; pass `False` if a deployment is unfitted. Cortex-side `fit_temperature` applies only to backends that return logits.
- Rules-v0 probabilities are the rules confidence on the chosen tier with the remainder spread evenly; this is a presentation, not a measurement, hence `calibrated=False`.
- The abstain threshold default 0.5 is not tuned; no dev set exists for tier routing yet.

## Not done

- No trained model, no weights, no dev set for the tier question. `automatable_share` and `ece` are ready for one.
- No HTTP endpoint (YAGNI).
- Pre-existing `mypy` failures (41 errors in 26 files, none in the new or touched files) are outside this ticket.
