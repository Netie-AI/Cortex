# Crew wakes on the engine tree

| Date | Topic | Keywords | Main idea |
|------|-------|----------|-----------|
| 2026-08-28 | crew-wakes-engine-tree | crew, wakes, timer, completion, belt, control-display | Ported WakeStore to `E:\Cortex\CortexOS\crew\wakes.py`. Timer/completion persist. Run finish marks completion due. 5s tick posts `[wake]` into the transcript. GET `/crew/belt` and `/v1/belt` carry wakes. Control displays; never POSTs. |

Do not rebuild on `E:\Cortex-run-timeout` or grow `E:\Cortex-crew`. Verify: `python -m pytest tests/test_crew/test_wakes.py tests/test_crew/test_server.py -q` from `E:\Cortex`.
