---
keywords: [github, pat, fine-grained, netie-ai, helio, cursor-app, access, jian-hong, 404]
main_idea: A jian-hong fine-grained PAT is not Netie-AI org access. It 404s private Cortex. Cursor All-repos on Helio.AI is the wrong org for this checkout. Grant Cursor on Netie-AI. Revoke chat-pasted tokens.
---

# Fine-grained PAT does not unlock private Netie-AI

## Main idea

This was not a hallucination. `GET /user` as `jian-hong` succeeded. The same
token still 404s `Netie-AI/Cortex` (private) and every private product name
the operator screenshot confirmed. Cloud `gh` (Cursor GitHub App on this
repo) can read Cortex and still 404s `dms` / `Netie-KB` / `Pointer`.

## Probe (2026-08-25, token not stored)

- Token kind: fine-grained (`github_pat_` prefix).
- Header: `x-accepted-github-permissions: allows_permissionless_access=true`.
- `/user/orgs`: empty list.
- Org membership (`Netie-AI`, `HelioAI`, `helio`): 403 Resource not
  accessible by personal access token.
- `GET /orgs/Netie-AI/repos`: 10 public names only. Cortex not in the list.
- `GET /repos/Netie-AI/Cortex` with PAT: 404. Same URL with cloud `gh`: 200
  private.
- `Helio.AI` is not an API owner slug (404). `HelioAI` exists, 0 repos
  visible to that PAT. `helio` is an unrelated carbon-aware cloud org.

## Cursor screenshot vs this agent

The phone screenshot is Cursor GitHub App on **Helio.AI**, All repositories.
This checkout is **github.com/Netie-AI/Cortex**. Installing Cursor on Helio.AI
does not grant this cloud agent Netie-AI private product trees.

## What to grant

1. Revoke the PAT that was pasted in chat (it is burned).
2. Netie-AI org -> Settings -> GitHub Apps -> Cursor -> Repository access ->
   All repositories (or at least dms, Netie-KB, Pointer, landing, Space,
   netie-control, RUMA-Houser, ViKing, Netie).
3. If products actually live under HelioAI, say so and add that org to
   `CREW_GH_OWNERS`. Do not paste a new PAT in chat; OpenVault / env only.

## Golden rule

> Public list + 404 is not absence. Permissionless PAT is not org access.
> Wrong-org Cursor App is not this agent. Rotate leaked tokens.
