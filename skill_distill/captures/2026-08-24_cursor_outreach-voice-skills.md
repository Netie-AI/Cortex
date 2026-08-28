# Capture template

```yaml
id: 2026-08-24_cursor_outreach-voice-skills
source: cursor
date: 2026-08-24
operator: Jian Hong / Cursor Grok 4.6
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: promoted
```

## Raw answer

Business orchestration chat produced a working Crew (detect, spawn, capability templates, Teach skills). Gap: tone lived in one Cursor skill (`outreach-human-tone`) and a 3-line `tone.md`. Spawned teammates did not inherit outreach/chat/build playbooks. Operator asked to (1) add more skills/tones, (2) pass them to other agents and Cortex-crew, (3) research public speaking, (4) teach computer-reach, (5) offload internal decide/build/maintain prompts.

Public copy research used (not scraped AppData):

- Allston Labs cold-copy: specificity, brevity 50-125 words, recipient-orientation deletion test, explicit binary ask. Founder voice ~2x SDR on same offer.
- 2026 inbox filters punish merge-field templates with one city swapped.

Netie constraints kept: MONEY_LANE 4h gate, public mailbox, draft-only, human sends, no Grok AppData, no overnight CRM.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Spawn copies `skills=` into role_prompt but Manager rarely passed it | `runtime.py` `_spawn` | high | skill |
| Capability templates can carry default skill names | `roles.py` Role.skills | high | skill |
| data/crew is gitignored so packs must live under CortexOS/crew/skill_packs | `.gitignore` | high | rule |
| Local Teach files must win over shipped packs | operator Teach UI | high | skill |
| ADHD Cursor voice leaks into factory mail if not split | 22 Aug extract wave | high | skill |

## Action YAML

```yaml
promote: skill
paths:
  - CortexOS/crew/skill_packs/
  - CortexOS/crew/board.py
  - CortexOS/crew/roles.py
  - CortexOS/crew/detect.py
  - CortexOS/crew/runtime.py
  - ~/.cursor/skills/outreach-human-tone
  - ~/.cursor/skills/chat-human
  - ~/.cursor/skills/voice-learn
  - ~/.cursor/skills/computer-reach
  - ~/.cursor/skills/software-build
  - ~/.cursor/skills/software-maintain
  - ~/.cursor/skills/operator-decide
```

## Netie implications

- Build now: skill packs + auto-copy on spawn (this capture).
- Park: unattended send, LinkedIn bots, AppData reverse-engineer.
- Tests required: test_crew board/detect/roles as named in the finding.

## Citations

- distill: skill_distill/captures/2026-08-24_cursor_outreach-voice-skills.md
- https://allstonlabs.com/library/copy/principles
