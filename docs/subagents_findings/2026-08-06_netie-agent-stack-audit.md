```yaml
keywords: [prd-agent, epic-agent, ticket-runner, model-routing, NEEDS-YOU, unlocks, AGENT_SYSTEM, cursor-subagents, composer, grok-4.5, opus, F17, F18]
main_idea: "PRD→Epic→Ticket law is solid in AGENT_SYSTEM + Claude Code agents only; Cursor lacks matching defs; four routing files conflict; no central Unlock board — seed from AirGPT F17–F20 and peer NEEDS-YOU rows."
models: [cursor-grok-4.5-high]
workflow: netie-control-fork
reuse: golden_rule
status: verified
cite: agent:f2846e0b-c6da-43df-b2af-299b9b435cd0
repo: multi
date: 2026-08-06
```

# Netie PRD→Epic→Ticket stack audit

## Main idea

- Canon: `D:\Netie\Internal\Agents\AGENT_SYSTEM.md` + Claude Code `~/.claude/agents/{prd,epic,ticket-runner}.md`.
- Cursor has routing rules but **no** deployed prd/epic/ticket-runner subagent defs.
- `agent-model-routing.mdc` wrongly puts ticket-runner on Grok; overnight/plan-grok rules already use Composer for exec — unify to Opus/Grok judgement + Sonnet/Composer exec.
- No estate Unlock board; seed from AirGPT F17–F20, Pointer F12/F17, OpenVault F13–F14, DMS F26/F33.
- Ticket Runner prompt lacks mandatory Execution plan / exact commands / stack preflight — Epic Agent must refuse incomplete tickets.

## Keywords (search)

`prd-agent`, `epic-agent`, `ticket-runner`, `model-routing`, `NEEDS-YOU`, `UNLOCKS`, `F17`, `F18`, `Composer`, `Grok`, `Opus`, `sync_plan_issues`

## Questions left open

- Cursor-native subagent file format vs AGENTS.md paste?
- Build `sync_plan_issues.py` before or after Netie Control GitHub connector?

## Full answer / evidence

Deployed Claude agents: `C:\Users\OoiJianHong\.claude\agents\prd-agent.md`, `epic-agent.md`, `ticket-runner.md`.

Cursor conflict: `agent-model-routing.mdc` (Grok for ticket-runner) vs `plan-grok-implement-composer.mdc` / overnight plans (Composer build).

Missing: `scripts/sync_plan_issues.py` (specified in DOCUMENT_SYSTEM, not built). Estate `D:\Netie\STATUS.md` stale 2026-08-03.

Unlock seed (severity):

| Product | IDs | Severity |
|---------|-----|----------|
| AirGPT | F17, F19 | HIGH (+ trunk WIP blocker) |
| AirGPT | F18 | MEDIUM (dead schedule_hint = defect) |
| AirGPT | F20 | LOW SURFACE |
| Pointer | F12/F17 boundary | CRITICAL/HIGH |
| OpenVault | F13, F14 | HIGH |
| DMS | F26 DeepSeek PDPA | HIGH |

## Golden rule (if reusable)

> One routing matrix only: PRD/Epic = Opus (Claude) / Grok 4.5 high (Cursor); Ticket Runner = Sonnet / Composer; Verifier = different session + strong model. Epic tickets without `Execution plan:` + exact commands + `Verify command:` are refused. NEEDS-YOU items must land on a central Unlock board (`UNLOCKS.md` or Netie Control) with Severity | Blocks | Default recommendation — never only buried in PRD FAQ rows.

## Verify

```bash
Test-Path C:\Users\OoiJianHong\.claude\agents\prd-agent.md
Test-Path D:\Netie\Internal\Agents\AGENT_SYSTEM.md
rg -n "ticket-runner" C:\Users\OoiJianHong\.cursor\rules\agent-model-routing.mdc
```

## Promote?

- Patch AGENT_SYSTEM + agent-model-routing on Netie Control Wave 3 / immediate if founder wants unlock board first.
