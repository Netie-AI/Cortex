## Parallel subagent policy
Research subagents: parallel OK. Output = markdown summary to `docs/research/`.
Feature subagents: sequential only. One at a time. Claude gate between each.
A research subagent NEVER creates or modifies Python, SQL, or JSX files.

## npm/ staging folder
The `npm/` directory holds exported/staged files from prior Cursor sessions.
After integration into the repo, source of truth is the canonical paths under `packs/`, `CortexOS/`, `scripts/`, `tests/`.
Do not edit `npm/` copies — edit canonical paths only.
