# Cortex

Netie Cortex: governed agentic runtime for warehouse/logistics SMEs.

## Handoff (read first)

| File | When |
|------|------|
| [STATUS.md](STATUS.md) | Current gate, debt, next feature |
| [CONTEXT.md](CONTEXT.md) | Paste into new Claude chat |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Built vs partial inventory |
| [PARKING_LOT.md](PARKING_LOT.md) | Deferred ideas |

```powershell
python scripts/handoff.py   # clipboard-ready block for Claude
```

## Quick start

```powershell
pip install -e ".[dev,api,dms]"
.\demo\run_demo.ps1
```

- UI: http://localhost:3000
- Warehouse: http://localhost:3000/warehouse
- API: http://localhost:8000/health

## Tests

```powershell
python -m pytest tests/ -q
```

## Docs

See [docs/README.md](docs/README.md) for build plans, gates, and Cursor governance.
