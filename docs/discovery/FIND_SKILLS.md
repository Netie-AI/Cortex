# Discovery + Playwright reliability

## What shipped

- Offline reference catalogs from awesome lists (skills → MCP → subagents)
- `find_skills` / `find_mcp` / `find_subagents` tools (MCP + agent broker + HTTP)
- SkillOpt evolve seed (`evolve=true`) → `data/discovery/skillopt/*.best_skill.md`
- Playwright reliability suite (Python + demo UI)

## Reliability checklist (run before claiming green)

1. Unit: `python -m pytest tests/test_discovery tests/dms/test_discovery_routes.py -q`
2. Playwright (API in-process): `python -m pytest tests/reliability/test_playwright_discovery.py -q`
   - Requires: `pip install playwright uvicorn` and `playwright install chromium`
3. Stress: `python -m bench.stress --scenario discovery --threads 8 --iterations 20`
4. Demo UI E2E (API on :8000 + UI):
   - `PACK=dms DMS_AUTH_DISABLED=1 uvicorn CortexOS.api.main:app --port 8000`
   - `cd demo/dms-ui && npm i && npx playwright install chromium && npm run test:e2e:reliability`

## Policy

Skills first from GitHub refs + local SkillCards; MCP/subagents as fillers; SkillOpt for offline evolution. Third-party MCP *clients* remain P16-gated — discovery indexes them for recommendation only.
