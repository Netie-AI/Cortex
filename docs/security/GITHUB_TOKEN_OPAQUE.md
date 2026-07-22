# GitHub App / Actions token format (opaque)

**Date:** 2026-07-22  
**Changelog:** [Per-request override header](https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header/) · [Apr notice](https://github.blog/changelog/2026-04-24-notice-about-upcoming-new-format-for-github-app-installation-tokens/)

## What changed

GitHub App installation tokens (and Actions-issued `GITHUB_TOKEN` / `ghs_` class) are rolling to a **stateless JWT-shaped** format (~520 chars, still prefixed `ghs_`). Classic opaque short tokens remain during rollout.

Temporary opt-in/out on `POST /app/installations/:installation_id/access_tokens`:

| Header | Value | Effect |
|---|---|---|
| `X-GitHub-Stateless-S2S-Token` | `enabled` | Force JWT/stateless token |
| `X-GitHub-Stateless-S2S-Token` | `disabled` | Force classic opaque token |
| (absent) | — | Server rollout decision |

Header is **temporary** — remove after both formats validated.

## Cortex stance

1. **Treat all GitHub tokens as opaque strings** — no length caps, no `ghs_[A-Za-z0-9]{36}` assumptions.
2. **Secrets scanner** (`scripts/secrets_scan.py`) matches `ghp_` / `ghs_` / `gho_` / `ghr_` / `github_pat_` with a long opaque body (`{20,}`), including JWT-shaped `ghs_` fixtures in tests.
3. This repo does **not** mint installation tokens via the Apps API today (Actions uses the runner-provided `GITHUB_TOKEN`). No production code needs the override header unless we add a GitHub App integration later.
4. When/if we mint installation tokens: validate once with `enabled` and once with `disabled`, then drop the header.

## Do not

- Store tokens in `varchar(255)` or similar.
- Validate token shape beyond “non-empty opaque secret.”
- Commit real `ghs_` / `ghp_` values (scanner will fail CI).
