---
keywords: [pointer, recall, space, ci, ecosystem, hud, default-off]
main_idea: Pointer recall HUD defaults off with v3 migrate-once. Space gained an honest windows-latest dotnet test job (EnvLoader mask+path). Goal open. Founder cards unchanged.
models: [cursor-grok-4.6]
workflow: 2026-08-27_pointer-recall-space-ci
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#5
repo: Cortex
date: 2026-08-27
---

# Pointer recall default-off + Space honest CI (2026-08-27)

PREFLIGHT: HIT - analog table named Pointer recall-on-disk and Space no CI.

## Main idea

Pointer 60s expiry already shipped (PR #31). Remaining defect was default-on, no HUD control. Fresh installs and pre-v3 settings keep recall off. `#set-recall` is the opt-in. Do not merge Pointer PR #1.

Space had no test project, so `dotnet test` on the WinExe would exit 0. Four EnvLoader tests can fail. GitHub installs SDK 8.0.x. This laptop has the host and zero SDKs; local script names SKIP.

## Golden rule

> Do not invent green. A skip is named. A default is off until opted in.

## Verify

```
node E:\Pointer-wt-recall-off\test\invariants\recall-default-off.test.js
powershell -File E:\Space-wt-ci\scripts\ci-test.ps1
```

## Do not

- dms#61, work.netie.ai, grok-bot copy, Pointer PR #1 merge, HT1-HT5, n8n clone
