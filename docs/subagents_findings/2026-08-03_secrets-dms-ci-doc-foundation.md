---
keywords: CORTEX_CONTRACT_TOKEN, org-secrets, private-repo, mypy, dependabot, feedback-ledger, TAS, netie-init
main_idea: Org secrets cannot reach private repos on current plan; repo-level CORTEX_CONTRACT_TOKEN unblocks DMS Cortex checkout; CI then fails mypy (19); feedback ledger must live in Netie PRD with CLAUDE/.cursor pointers or it collapses across chats.
---

# Finding: DMS token + doc foundation (2026-08-03)

## Secrets

- GitHub org secrets do not apply to private repos on the current plan.
- Fix: `gh secret set CORTEX_CONTRACT_TOKEN --repo Netie-AI/dms` from `D:\NetieSecrets\GITHUB_SECRETS.md`.
- Classic PAT with `repo` that can see `Netie-AI/Cortex` returns HTTP 200; fine-grained needs resource-owner Netie-AI + approval.
- After token set, checkout step passes; next failure is mypy (19 errors in 6 files) - dms#7.

## Dependabot (Cortex)

- Critical (3): litellm host-header auth bypass, litellm SQL injection in proxy API key path, Next.js middleware auth bypass in `demo/dms-ui`.
- High (40): mostly Pillow decompression/heap issues + litellm MCP/OAuth + postcss + transformers RCE paths.
- These are dependency CVEs, not proof the demo surface is exposed. Prioritize litellm (engine dep) and ignore or isolate demo lockfiles until demo UI is customer-facing.

## Feedback ledger anti-collapse

- Ledger table lives in `D:\Netie\Software Blueprint\<Product>\PRD-###.md` section Feedback ledger.
- Every product repo cold-start loads `CLAUDE.md` NETIE block + `.cursor/rules/netie-system.mdc` pointing at that path.
- Without opening Netie, agents only get the pointer - they must open Netie or the ledger is invisible. That is intentional (one memory), not a second copy.

## Verify

```powershell
(Get-Content D:\NetieSecrets\GITHUB_SECRETS.md -Raw).Trim() | gh secret set CORTEX_CONTRACT_TOKEN --repo Netie-AI/dms
gh run list --repo Netie-AI/dms --limit 1
```
