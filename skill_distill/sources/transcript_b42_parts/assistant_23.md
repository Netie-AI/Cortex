Cursor multitask + model-routing distill is done and parked in the repo.

### Capture
`skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`

### How Cursor does it (this harness)
- **Coordinate:** multiple `Task` calls in one parent turn; children get **no** parent chat — parent must pass a full brief; return is **summary-only**; `resume` / `interrupt` / `environment: cloud`.
- **Model choice:** chat picker → agent YAML `model:` → Task `model` **allowlist only** (no silent swap). Observed: `composer-2.5` / `composer-2.5-fast` for explore/fan-out; frontier slugs for hard/review lanes.
- **Precision:** stacked rules/skills + Grep/Read/Glob + lazy MCP (`GetMcpTools` before call) + mode; agentic search, not silent blob RAG.

### Pipeline
- Ingest ran (5 captures).
- DISTILL Cursor “Composer vs frontier” ticked.
- P19 routing debt **settled**; allowlist note recorded as settled fact.
- `claude_code_vs_cursor.md` Cursor column upgraded (multitask + **Model routing** row).

### Promote / park
**Promote:** routing table; child-brief rule; explore=Composer / review=frontier.  
**Park:** nest depth, compaction thresholds, fallback chain, cloud MCP ≠ local (C1–C5 in the capture).