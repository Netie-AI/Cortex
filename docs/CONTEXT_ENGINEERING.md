# Context engineering (CortexOS)

Curates the **smallest high-signal token set** for agents — not prompt wordsmithing alone.

Canonical package: `CortexOS/context_engineering/`

| Module | Role |
|--------|------|
| `layers.py` | Instructions / tools / examples / memory / retrieval / state / messages |
| `budget.py` | Attention budget split + deterministic `fit_text` |
| `compaction.py` | Tool-result clearing + head summary |
| `notes.py` | `.airgpt/NOTES.md` structured note-taking |
| `assembler.py` | `assemble_context(ContextRequest) → AssembledContext` |

## HTTP

Registered always (not DMS-only):

- `POST /api/context/assemble`
- `POST /api/context/compact`
- `POST /api/context/notes/append`
- `GET  /api/context/notes?workspace_root=...`

## Integration

- **Ponytail** — prefetch path runs through `assemble_context` before tier budgets.
- **Engine registry** — optimizer id `context_engineering` (category `context`).
- **OpenIDE / AirGPT** — local-first twin at `AirGPT/context_engineering/` wired from `agent_runtime`.

See OpenIDE `docs/PROMPT_LAYERS.md` for the agent layer contract.
