```yaml
id: 2026-07-27_cursor_rag-authority-planes
source: cursor
date: 2026-07-27
operator: ooi
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: normalized
```

## Raw answer

AirGPT Space 5 ("Good Good") held the netie fabrication in 1178 conversation chunks.
Authority layer now contains it: purge reclaims storage; correctness is already enforced
by never-cite conversation + `chats_as_evidence=false`.

Architecture decision (keep three planes separate, one safe cross):

- Cortex agentic memory, RAG corpora, and the Memory Agent must not merge.
  Merging is how a chat guess became "evidence".
- Facts the **user asserts** (not model output) may promote to a global layer
  readable across RAG spaces.
- Safety rule already built: authority travels with the fact.
  User-asserted → citable everywhere; model-generated → citable nowhere.
- Multiple RAG spaces per topic sharing that global layer is coherent;
  a global model-output layer is not.

Session follow-through (2026-07-27):
- Purged Space 5 chats (8 sources / 1178 chunks); left 114 web chunks.
- Origin-keyed `scrub_authority` + source `meta.authority` / `original_path`.
- File-scoped ask-bar picker wires `source_ids`.
- Async `/sources` + create-flow job poll.
- Cortex engine live on :8010 (detached uvicorn).

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Keep Cortex memory / RAG / Memory Agent as separate planes | observed (netie cite loop) | high | rule |
| Only user-asserted facts may promote to a cross-space global layer | inferred + architecture note | high | rule |
| Authority must travel with the fact (ingest-time, survive export) | observed AirGPT fix | high | skill |
| Path-only classify is a fallback; pack prefixes defeat it | test_authority_roundtrip | high | none |

## Action YAML

```yaml
- id: planes-separate
  promote: rule
  text: >
    Never merge Cortex agentic memory, RAG corpora, and the Memory Agent.
    Model output must not become cross-plane evidence.
- id: user-assert-global
  promote: parking
  text: >
    Promote only user-asserted facts to a global layer readable across RAG spaces;
    authority travels with the fact.
  condition: after AirGPT global-claim store exists
```

## Netie implications

- Build now: keep authority on every ingested fact; refuse to cite conversation.
- Park: cross-space global layer for user assertions only.
- Tests required: authority roundtrip + scrub from original_path.

## Citations

- distill: skill_distill/captures/2026-07-27_cursor_rag-authority-planes.md
