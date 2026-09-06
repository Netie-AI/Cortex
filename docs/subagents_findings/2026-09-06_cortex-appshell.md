# Cortex AppShell: compose operator planes around Crew

- **Date:** 2026-09-06
- **Keywords:** appshell, crew, control, f-0030, apps launcher, audit, constructor, cmd-k, mobile
- **Main idea:** Week IA operator shell wraps Crew chrome with nav + Apps launcher + Control/Audit GET deep-links. Planes stay separate. Control is display-only F-0030; launchers are not spawn. Audit paints CERTIFIED|ABSTAIN|REFUSE from rsf_operate with no invent-green.
- **Verify:** `python -m pytest tests/test_crew -q` (246 passed locally after rebase onto #192)
- **Does not prove:** live `:8020` (not started); Control `:8040` up; GitHub CI; bottom-nav PWA beyond a tiny webmanifest
- **Cite:** CORTEX-APPSHELL week IA; off freeze #4/#41-#44; rebased after #192 FACTS-MD
