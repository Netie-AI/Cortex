---
keywords: [crew, skills, outreach, tone, spawn, passdown, voice-learn]
main_idea: Shipped skill packs auto-copy into spawned teammates by capability. Local Teach files win. Cursor chats get the same skills under ~/.cursor/skills.
---

# Crew skill pass-down

## Main idea

Business-orchestration voice was only in one Cursor skill. Spawned Crew teammates did not get it. Canonical packs now live in `CortexOS/crew/skill_packs/*.md` (git). Boot copies missing files into `data/crew/skills`. `spawn_agent` unions capability default skills into the teammate role prompt. Detect tells the Manager which skills will attach. Public founder-copy research (specificity, deletion test, 50-125 words, draft-not-send) landed in `outreach.md`. Chat replies, voice-learn, computer-reach, decide, build, and maintain are separate skills so ADHD Cursor voice never leaks into a factory inbox.

## Golden rule

> Detect the job, copy the matching skill into the teammate, never overwrite a Teach file. Human sends.

## Verify

```bash
cd D:\Cortex-crew
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew/test_board.py tests/test_crew/test_detect.py tests/test_crew/test_roles_mcp.py -q
```

## Packs

| File | Who gets it |
|------|-------------|
| outreach.md | Marketing, Email |
| chat-human.md | Marketing |
| voice-learn.md | Skills |
| computer-reach.md | Email, Browser, Connector |
| decide.md | Money, Decision |
| build.md | Gate, PR |
| maintain.md | Ticket, Routines, Watchdog |

Cursor pass-down: `~/.cursor/skills/{outreach-human-tone,chat-human,voice-learn,computer-reach,software-build,software-maintain,operator-decide}` and copies under `D:\Cortex-crew\.cursor\skills`.
