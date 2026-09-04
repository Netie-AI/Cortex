Skill-route: pick an existing playbook. Do not create a fourth skill store.

distill: skill_distill/captures/2026-08-25_cursor_kanhseei-feedback-loop.md

Stores: ~/.cursor/skills, CortexOS/crew/skill_packs, data/crew/skills (Teach, local wins), Cortex-crew/skills/*.yaml SkillCards, skill_distill/.

Route:
- first-touch mail -> outreach
- they wrote back -> chat-human
- auth scare / who approved -> chat-human then feedback-learn
- mock HTML deck video -> proposal-artifact + computer-reach
- send/click/Gemini/Flow -> computer-reach then decide
- turn a lesson into a skill -> feedback-learn then distill ingest
- named skill not on the roster / skill storage / GitHub skill pack -> skill-ingest (search, fetch, label, save_skill)
- make a website / clone a public page / 3d page -> analog-surface, then skill-ingest for any missing named pack
- which skill or MCP -> this pack, then Cortex find_skills
- feature / PRD / reuse a prior-repo UI or schema -> build.md + TAS analog map. PRD agent first. Control GET /v1/plans. Do not redesign tokens/layout.
- Palantir / Foundry / AIP / ontology / semantic layer / client intake / Constructor canvas -> ontology-foundry, then build.md. P1 parked.

No mcp-skill-storage. Crew spawn already inlines named packs. SkillMesh already retrieves SkillCards by intent.
