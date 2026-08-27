---
keywords: [kanhseei, outreach, authorization, rm500, coming-soon, feedback-learn]
main_idea: Website-fix cold mail plus an RM price is read as unauthorized live work; first-touch stays text; demos after they ask; no fourth MCP skill store.
---

# 2026-08-25 Kanhseei auth feedback

cite: distill: skill_distill/captures/2026-08-25_cursor_kanhseei-feedback-loop.md
cite: Gmail thread 1a03200b47b3aebe

## What happened

Unsolicited Coming Soon + `RM 500, one revision`. Steven asked for contact details only. Draft plus "tidy onto the live URL" went to engineering. Lokesh treated it as unauthorized site work and an unpaid charge.

## Golden rule

Contact-details != permission. Quote != charge. First-touch = plain text. HTML/PPTX/video only after they ask, watermarked DRAFT, not on their domain.

## Verify

```
cd D:\Cortex-crew
python -m pytest tests/test_crew/test_detect.py tests/test_crew/test_roles_mcp.py -q
```

Expected: green, including `test_feedback_and_auth_detect`.
