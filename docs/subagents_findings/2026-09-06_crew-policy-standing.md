```yaml
keywords: [crew, policy, standing, allowlist, session, one-off, circuit-breaker, epic-116]
main_idea: "Settings ladder is Crew policy SoT. decide/decide_with_approvals enforce standing/session/one-off. Repeated deny trips circuit-breaker. Per-agent grants only tighten."
models: [grok-4.6]
workflow: crew-policy-standing
reuse: 2026-09-06_crew-belt-claim
status: verified
cite: agent: crew-policy-standing
repo: Cortex
date: 2026-09-06
```

# CREW-POLICY-STANDING server ladder

PREFLIGHT: PARTIAL
reuse: 2026-09-06_crew-belt-claim, 2026-09-04_crew-facts-md-hud
spawn: skip

## Gap

Chrome Settings painted one-off / session / standing allowlist and a
circuit-breaker, but the ladder lived in `localStorage`. `policy.decide` still
treated every mutating MCP call as one-off.

## Landed

- `CortexOS/crew/approvals.py` — crew-wide book. Standing + circuit persist
  under `data/crew/approvals.json`. Session stays in-process.
- `policy.decide` / `decide_with_approvals` — standing/session may only turn
  `confirm` into `allow`. Deny, master-off, unarmed, and agent grants stay the
  floor. Login walls never auto-allow.
- Confirm POST records approve/deny. Three denies trip the circuit-breaker
  (standing revoked, mode back to one-off).
- Chrome Settings reads/writes `/crew/approvals` instead of localStorage.
- Parent #116. No second epic. Control untouched. Freeze lanes not attached.

## Verify

```
python -m pytest tests/test_crew -q
```

## Not this slice

Live `:8020` until founder restart. Control HTML. GitHub CI not claimed from
this agent. `close_issue` HITL still always confirms (not `policy.decide`).
