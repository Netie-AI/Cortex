Build: smallest change that works. Ponytail ladder. Cortex invariants.

Ladder, stop at first rung that holds:
1. Need it? 2. Already here? 3. Stdlib? 4. Native? 5. Installed dep? 6. One line? 7. Then minimum.

Rules:
- Read the real flow first. Do not skip trust-boundary checks or what the operator asked.
- No git add -A. No secrets. No CortexOS importing packs (C2). duckdb only under execution/.
- Do not weaken manifest refusals or hand-edit contract specs.
- One small runnable check. Name the exact command. Do not claim green unless it ran.
- Cursor model: grok-4.6 high, not fast. Do not spawn one cloud agent per issue.

Verify default for crew code:
cd D:\Cortex-crew
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew -q
