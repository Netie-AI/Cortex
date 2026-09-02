---
id: 2026-08-27_cursor_ticket-runner-seated-writer
source: cursor
date: 2026-08-27
operator: jian-hong / ticket-runner session
prompt_used: founder seated-writer prompt (DMS #31 XLSX-ORCH-11) — copy for next time
distill_trace: skill_distill/DISTILL.md
status: raw
---

# Capture: seated Ticket Runner writer prompt

## Raw answer

Founder instruction (2026-08-27): distill this prompt shape so the next
Ticket Runner copy-paste does not re-derive it. This capture is the
prompt that executed DMS #31 (XLSX-ORCH-11) plus the parallel-seat
rules that ran with it.

### Prompt to copy (seated writer, one named ticket)

```
You are the Ticket Runner for <REPO> issue #<N> (<TICKET-KEY>) in repo
<ABS-PATH>. Use POSIX git (WSL: /mnt/<drive>/... ; not a stale checkout).

READ FIRST, BEFORE ANY EDIT — these bind and override your defaults:
- <repo>/CLAUDE.md — hard rules. Name the ones that bite this ticket.
- <repo>/AGENTS.md, docs/ACTIVE.md, STATUS.md.
- gh issue view <N> and gh issue view <PARENT>. Follow the ticket's
  own acceptance and agent prompt exactly.

BRANCH HYGIENE:
- Do NOT build on a stale/cursor/* branch. Confirm git status --short.
- Ignore named untracked noise (e.g. .venv_audit/). Leave unrelated
  stashes alone.
- If anything else is dirty, STOP (R-0008 — never destroy uncommitted
  work). Prefer: git fetch origin && git worktree add -b feat/<key>
  <ABS-PATH>-wt-<key> origin/main
- One writer per branch (F-0019, F-0025). Claim the ticket first.

REUSE: name the existing functions/ports/fixtures. Do not reinvent.
YAGNI: stdlib / already-here / one function / then minimum.

WHY A HARD RULE DOES NOT FIRE — if the ticket looks like it violates
a hard rule, state the distinction in the commit message. If you would
have to violate it, STOP.

BUILD: the ticket acceptance, nothing else. Additive migrations only.
Do not invent a de-confliction the ticket did not settle — implement
the unambiguous part and report the rest to the founder.

GATE (mandatory, R-0007):
- Fixture built in-repo so the check runs on any machine and never
  skips (R-0002). If a dep is missing, FAIL with the cause named.
- Prove the gate bites: stash the implementation, run the gate,
  confirm FAIL; unstash, confirm PASS. Report both verbatim.
- Assert the customer artifact (R-0001), not an intermediate.

RUN the repo's own test command (CLAUDE.md / README / pyproject /
Makefile / scripts/ci_local.sh). Full suite too. Timeout 900. If the
suite needs a DB or service that is not up, say so — do not fake a
pass.

COMMIT on the feature branch. Do NOT push / PR / merge / touch main
unless the founder said so. Message names repo#N / parent epic,
states hard-rule reasoning, ends with the required trailer.

REPORT BACK: files + line counts, migration name, before-fix FAIL and
after-fix PASS quoted, full-suite result, commit sha, and an honest
statement of what the ticket still does NOT do (blockers named).
```

### Parallel seat (manager heartbeat, not one-agent-per-issue)

```
Survey gh. Classify READY / BLOCKED / HELD / HAS-WRITER.
WIP = two epics in flight. Many tickets under those two may run.
Seat READY tickets on isolated worktrees. One writer per branch.
Do not attach a second writer to a held/cursor/* branch.
Verify is a different run (R-0003). Do not close from the implementer.
Unblock cascade: newly unblocked tickets are READY next survey.
If a cross-repo blocker is in scope and the founder said so, seat a
writer in that other repo on a new unused branch — do not widen.
```

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Stale checkout is the default failure; worktree from origin/main | DMS was on cursor/status-f71-merge-wave, 1 ahead / 5 behind | high | skill |
| Gate must FAIL without the impl (stash protocol) | R-0007 + founder #31 prompt | high | skill |
| Hard rule 5: byte-faithful copy != authoring a workbook | CLAUDE.md + founder #31 prompt | high | none (already a hard rule) |
| Never skip; missing dep = named FAIL | R-0002 | high | skill |
| Report what the ticket still does NOT do | founder #31: live input still dms#30/Pointer | high | skill |
| One writer per branch; claim first | F-0019 F-0025 FLEET.md | high | skill |
| Distill next-time copy lives in this capture | founder 2026-08-27 | high | skill |

## Action YAML

```yaml
promote: skill
id: S-0005
title: Seated Ticket Runner writer — worktree, bite-gate, honest leftover
cite: distill: skill_distill/captures/2026-08-27_cursor_ticket-runner-seated-writer.md
```

## Netie implications

- Build now: S-0005 in Netie-KB; thin Cursor pointer if needed.
- Park (condition): none. This is copy-paste operational.
- Tests required: the stash-FAIL / unstash-PASS protocol IS the test.

## Citations

- distill: skill_distill/captures/2026-08-27_cursor_ticket-runner-seated-writer.md
