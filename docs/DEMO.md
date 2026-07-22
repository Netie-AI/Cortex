# DMS Demo — show in 60 seconds

## One command (Windows)

```powershell
cd C:\Users\user\RUMA\Cortex
.\demo\run_demo.ps1
```

**Fast restart** (skip dataset regen if already built):

```powershell
.\demo\run_demo.ps1 -Fast
```

## URLs (after script says OK)

| Page | URL | What to show |
|---|---|---|
| NL Query | http://localhost:3000 | Ask: *How many SKUs are below reorder level?* |
| Warehouse | http://localhost:3000/warehouse | Intake item → confirm dims → scan move |
| Chat | http://localhost:3000/chat | Inbound message → intent classify + security |
| Data | http://localhost:3000/data | 25k-row linked DuckDB tables |
| Audit | http://localhost:3000/audit | Hash-chained ledger entries |
| API health | http://localhost:8000/health | `{"status":"ok","pack":"dms"}` |

## 3-minute pitch flow

1. **Query** — natural language → SQL guardrail → DuckDB answer + chart
2. **Warehouse** — photo intake (EXIF stripped), dimension confirm, QR bin tree
3. **Chat** — paste scam message → blocked/classified; paste stock question → intent detected
4. **Audit** — every write logged to F1 ledger

## If something fails

**UI shows blank / 500:** Next.js dev server needs an empty `demo/dms-ui/pages/` folder (App Router quirk). Fixed in repo; or run:
```powershell
mkdir demo\dms-ui\pages -Force
Remove-Item -Recurse -Force demo\dms-ui\.next -ErrorAction SilentlyContinue
.\demo\run_demo.ps1 -Fast
```

```powershell
.\scripts\diagnose_demo.ps1
Get-Content demo\logs\api.err.log -Tail 30
Get-Content demo\logs\ui.err.log -Tail 30
```

## Prerequisites

- Python 3.10+
- Node.js 18+ (`npm`)
- `env.local` in repo root (copy from your main machine — see [SETUP_NEW_MACHINE.md](SETUP_NEW_MACHINE.md))
- `pip install -e ".[dev,api,dms]"` (run_demo does this automatically)

## Stop demo

```powershell
# Kill listeners on demo ports
netstat -ano | findstr ":8000"
netstat -ano | findstr ":3000"
# Stop-Process -Id <PID> -Force
```
