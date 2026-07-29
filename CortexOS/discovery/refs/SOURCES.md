# Cortex discovery references

Offline-first catalogs under `CortexOS/discovery/refs/` power **Find Skills**.

## Policy

1. **Skills first** — local SkillCards + awesome skill lists
2. **Then MCP / subagents** — if skills are thin for the goal
3. **Evolve with SkillOpt** — optional `evolve=true` seeds `best_skill.md` for offline training

## Sources

| Role | Repo |
|------|------|
| MCP servers | https://github.com/punkpeye/awesome-mcp-servers |
| Toolkit / subagents | https://github.com/rohitg00/awesome-claude-code-toolkit |
| Claude skills | https://github.com/BehiSecc/awesome-claude-skills |
| Agent skills | https://github.com/itgoyo/awesome-agent-skills |
| Evolve | https://github.com/microsoft/SkillOpt |
| Meta find-skills | https://github.com/vercel-labs/skills |

## Refresh

```bash
python scripts/refresh_discovery_refs.py --fetch
```

## Tool

```text
find_skills(goal="playwright e2e testing")
# ≈ Are there any good skills for [playwright e2e testing]?
```
