```yaml
id: 2026-08-27_claude-code_workflows-runtime
source: claude-code
date: 2026-08-27
operator: cursor-agent (Cortex scale/settle)
prompt_used: skill_distill/prompts/ASK_CLAUDE_CODE.md
distill_trace: skill_distill/DISTILL.md
status: raw
```

## Raw answer

Observed live on this laptop, 2026-08-27, from Claude Code workflow run artifacts under
`C:\Users\oojia\.claude\projects\` (not inferred from marketing). Four workflows ran
the same afternoon:

| Workflow | Repo | Phases | Agents | Outcome |
|----------|------|--------|--------|---------|
| `cortex-recon-decompose` | E:\Cortex | Survey, Rank | 12 | Survey 11/11 done (70 items, 43 startable). Rank died on session limit. |
| `dms-ground-truth-and-connector-plan` | E:\DMS | Ground truth, Synthesize | (scripted 5+1) | ran |
| `netie-ticket-triage` | E:\Pointer | Repo triage, Blocker chain | (one agent per repo) | ran |
| `verify-wp002` | E:\Netie | Verify | 3 parallel lenses | 26 findings, completed |

Runtime shape (from `wf_*.json` `script` field, a JS DSL compiled at run time):

1. `export const meta = { name, description, phases: [{title, detail}] }`
2. `phase('Survey')` then `await parallel(surveys.map(s => () => agent(prompt, {label, phase, schema})))`
3. Each `agent()` takes a HOUSE block (invariants + environment + startable definition), a task prompt, a JSON schema, and a label that becomes the step log.
4. `log(...)` writes the journal. Rank is a second `phase()` that consumes `JSON.stringify(good)`.
5. Return value is structured (`areas`, `raw_item_count`, `ranked`). Rank null means the fleet has no build list.

HOUSE rules that made recon honest: read-only, no held PRs (#4/#41/#43/#44), no founder-blocked issues (#12/#13/#17/#18/#42), never weaken `manifest.py`, never add `_C2_ALLOWLIST`, never hand-edit `contract/*.json`, duckdb only under `CortexOS/execution/`, RAM cap (targeted pytest only).

Step log: `subagents/workflows/<runId>/journal.jsonl` plus per-agent `agent-<id>.jsonl`. Parent sees labels (`survey:c2-allowlist`), token counts, toolCalls, durationMs, resultPreview. Rank failure is a first-class log line (`You've hit your session limit`) not a silent empty list.

Isolation: agents share the repo filesystem; they were told not to write. Cortex `workflow_oom.py` already shrinks local fan-out from RAM/VRAM; this recon still fanned 11 survey agents because they were read-only. Builders that run pytest must stagger (this box had ~1 GB free RAM).

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Claude Code workflows are JS scripts: meta + phase() + parallel() + agent(prompt, {label, phase, schema}) | observed | high | skill |
| Survey-then-rank is the estate recon shape; rank must be a separate phase that can fail without losing survey | observed | high | skill |
| Each survey agent returns the same item schema (id, startable, blocked_by, files, verify_cmd, risk, value, independent) | observed | high | skill |
| HOUSE block encodes invariants once; every child inherits them | observed | high | rule |
| Step log is label + tokens + toolCalls + resultPreview, not a hidden chat | observed | high | none |
| Adversarial verify is three parallel lenses (facts, constitution, completeness) with a findings schema | observed | high | skill |
| Ticket triage is one agent per repo then a blocker-chain phase | observed | high | skill |
| Ground-truth audit refuses STATUS.md as evidence | observed | high | rule |
| Session-limit can kill only the synthesis step; parent must rank locally and still build | observed | high | parking |
| Cortex templates already compile to dag_runner; adding recon/verify/triage/build templates does not add a second orchestrator | observed | high | skill |

## Action YAML

```yaml
promote: skill
id: cortex-workflow-templates-from-claude-code
into: CortexOS/execution/workflow_templates.py
cite: skill_distill/captures/2026-08-27_claude-code_workflows-runtime.md
templates:
  - recon_decompose
  - adversarial_verify
  - ticket_triage
  - build_and_verify
```

```yaml
promote: parking
id: P19-workflow-session-limit-rank
condition: Rank phase must checkpoint survey JSON so a session-limit cannot drop 70 items
cite: skill_distill/captures/2026-08-27_claude-code_workflows-runtime.md
```

## Netie implications

- Build now: four workflow templates + prompts, recognizer triggers that do not steal generic "survey" from deep_research.
- Park (condition): persist survey JSON before rank so a limit cannot null the build list.
- Tests required: catalog ids present; recognize("recon decompose tickets") -> recon_decompose; recognize("adversarially verify this paper") -> adversarial_verify.

## Citations

- distill: skill_distill/captures/2026-08-27_claude-code_workflows-runtime.md
