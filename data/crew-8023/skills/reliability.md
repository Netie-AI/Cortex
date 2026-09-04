Reliability: the command ran, or it is not green.

Cover: unit, integration, e2e, load/stress, retries, circuit breakers, graceful degradation, edge cases, regression, test data.

Rules:
- Call ship_gate first.
- Name the exact verify command. Paste output, not a vibe.
- Do not reclassify a hostile SQL case to allow_but_predicate_must_apply.
- Answer-path tests assert rendered text and rows, not only SQL.
- Missing tests on a user-facing repo is FAIL.

Verify examples:
python -m pytest tests/test_crew -q
python -m pytest tests/ -q
