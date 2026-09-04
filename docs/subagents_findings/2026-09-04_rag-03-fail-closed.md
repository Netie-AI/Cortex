```yaml
keywords: [RAG-03, rag_answer, RAG_KEYWORDS, fail-closed, space_id, acme_agreement]
main_idea: "Keyword RAG no longer short-circuits L0/L1. Misses abstain; first-file fallback gone. Doc stub only after L2-off abstain and only with space_id."
models: [grok-4.6]
workflow: solver
reuse: 2026-09-04_epic-015-verify.md
status: raw
cite: agent: rag-03-fail-closed
repo: Cortex
date: 2026-09-04
```

# RAG-03 fail-closed (Cortex#32)

PREFLIGHT: HIT
reuse: docs/subagents_findings/2026-09-04_epic-015-verify.md
spawn: skip

Served path was `route_question` `RAG_KEYWORDS` -> `answer()` early `route == "rag"` -> `rag_answer` first `.txt` (`acme_agreement.txt`).

Fix (narrow, Cortex envelope):

- Keep `blocked` first. Do not short-circuit on `rag`.
- After L0/L1/skill miss, run L2 if enabled; else space-scoped `rag_answer`.
- No `space_id` -> abstain (do not scan ungoverned `CONTRACTS_DIR`).
- `rag_answer` drops first-file fallback and stopword hits. No match -> empty sources, caller abstains.

Not in this PR: RAG-02 hybrid RRF / Qdrant / tantivy, `document_retrieval_port`, closing #33/#34.
