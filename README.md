# Cortex

Netie Cortex: governed agentic runtime for warehouse/logistics SMEs.

## Handoff (read first)

| File | When |
|------|------|
| [STATUS.md](STATUS.md) | Current gate, debt, next feature |
| [CURSOR_HANDOFF.md](CURSOR_HANDOFF.md) | Cursor builder startup |
| [CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md) | Claude supervisor paste |
| [docs/PLUG_AND_PLAY.md](docs/PLUG_AND_PLAY.md) | One-call `plug_in(app)` integration |

```powershell
python scripts/handoff.py --cursor   # builder
python scripts/handoff.py --claude   # supervisor
```

## Quick start (demo now)

```powershell
pip install -e ".[dev,api,dms]"
.\demo\run_demo.ps1          # first run (~2 min data build)
.\demo\run_demo.ps1 -Fast    # restart in ~30s
```

- UI: http://localhost:3000
- Warehouse: http://localhost:3000/warehouse
- Chat: http://localhost:3000/chat
- API: http://localhost:8000/health

**Show script:** [docs/DEMO.md](docs/DEMO.md)

## Tests

```powershell
python -m pytest tests/ -q
```

## Docs

See [docs/README.md](docs/README.md) for build plans, gates, and Cursor governance.
