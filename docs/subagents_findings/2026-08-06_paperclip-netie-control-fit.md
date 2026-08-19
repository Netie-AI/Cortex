```yaml
keywords: [paperclip, netie-control, heartbeat, adapters, claude-local, cursor-local, budgets, github-issues, prd-agent, epic-agent, ticket-runner, fork, company-model]
main_idea: "Paperclip is PARTIAL for Netie — strong Ticket Runner substrate (heartbeats, Claude/Cursor adapters, budgets); wrong planning layer until GitHub Issues connector + PRD/Epic law replace the AI-company model."
models: [cursor-grok-4.5-high]
workflow: netie-control-fork
reuse: golden_rule
status: verified
cite: agent:560987e1-79a9-4f80-954b-2668126fa56e
repo: multi
date: 2026-08-06
```

# Paperclip fit for Netie Control

## Main idea

- Paperclip FIT as execution control plane (wake Claude/Cursor on tickets with budgets + audit).
- MISS as PRD→Epic planning system of record without major fork: no GitHub Issues BYOT shipped, no irreversibility/refusal/R-0003, CEO-company metaphor tax.
- Fork strategy: keep server heartbeat + adapters + costs + slim UI; strip ClipHub/CEO onboarding/unused adapters; map Company→Product; GitHub stays durable tickets.

## Keywords (search)

`paperclip`, `netie-control`, `heartbeat`, `claude-local`, `cursor-local`, `budgets`, `github-issues`, `external-task-protocol`, `prd-agent`, `epic-agent`, `ticket-runner`

## Questions left open

- Implement `docs/specs/external-task-protocol.md` now vs mirror-only issues first?
- How aggressive to strip `heartbeat.ts` (~19k lines) vs wrap as-is?

## Full answer / evidence

Clone: `D:\Netie\paperclip` (MIT). Stack: Node 20, Express, Drizzle/PGlite, React 19 UI on `:3100`.

Adapters present: `packages/adapters/claude-local`, `cursor-local`, `cursor-cloud`, plus Codex/OpenClaw/Hermes/etc.

Strong keep: `server/src/services/heartbeat.ts`, `budgets.ts`, `costs.ts`, adapter registry, Dashboard/Issues/Agents/Costs pages.

Biggest gap: GitHub Issues connector is draft-only (`docs/specs/external-task-protocol.md`); Netie loop is GitHub-native (`owner/repo#N`).

Taxonomy mismatch: Paperclip Company→Goal→Project→Issue vs Netie PRD→EPIC(GH)→Ticket(GH) with parent-doc updates.

## Golden rule (if reusable)

> Use Paperclip (forked as Netie Control) only as the **execution control plane**: heartbeats, Claude/Cursor adapters, budgets, operator dashboard. Keep GitHub Issues + `Software Blueprint/` PRDs as planning source of truth. Do not adopt CEO/org onboarding as Netie law. Do not fork wholesale without stripping ClipHub/CEO assets and adding GitHub sync or an explicit mirror policy.

## Verify

```bash
Test-Path D:\Netie\paperclip\packages\adapters\claude-local
Test-Path D:\Netie\paperclip\packages\adapters\cursor-local
Test-Path D:\Netie\paperclip\docs\specs\external-task-protocol.md
```

## Promote?

- Into Netie Control Wave 0 plan + AGENT_SYSTEM routing notes when implement starts.
