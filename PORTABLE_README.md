# Cortex DMS — Portable SSD Demo

Copy this entire `Cortex` folder to any drive (e.g. `F:\Cortex` on a USB SSD).  
Works on **any Windows laptop** with Python 3.10+ and Node.js 18+.

## Quick start (other laptop)

1. Plug in the SSD (drive letter can be `D:`, `E:`, `F:` — does not matter).
2. Open the `Cortex` folder.
3. **Double-click `RUN_DEMO.bat`**
4. First run only: setup installs deps (~5–10 min). Then API + UI start automatically.
5. Open in browser:

| Page | URL |
|---|---|
| Query | http://localhost:3000 |
| Warehouse | http://localhost:3000/warehouse |
| Chat (F5 gate) | http://localhost:3000/chat |
| Brain | http://localhost:3000/brain |
| Skills (F6) | http://localhost:3000/skills |
| API health | http://localhost:8000/health |

**Tip:** Switch UI role to **DATA STEWARD** before enabling skill capture on `/skills`.

## Prerequisites (install once on demo laptop)

- [Python 3.10+](https://www.python.org/downloads/) — tick **Add python.exe to PATH**
- [Node.js 18+ LTS](https://nodejs.org/)

No Docker. Runs natively on the SSD.

## Scripts

| File | Purpose |
|---|---|
| `RUN_DEMO.bat` | **Double-click this** |
| `SETUP_ONCE.ps1` | First-time install (auto-run once) |
| `PORTABLE_DEMO.ps1` | Launcher logic; `-Fast` when DuckDB exists |
| `demo\run_demo.ps1` | API + Next.js process manager |

## Troubleshooting

See `docs\DEMO.md` or run `.\scripts\diagnose_demo.ps1`.

Re-run setup: delete `.portable-setup-done` then `.\SETUP_ONCE.ps1`.

## Not copied (by design)

- `env.local`, `key.md` — secrets stay on dev machine
- `node_modules`, `.next` — rebuilt by setup
- Optional: copy `data\dms_demo.duckdb` from a machine that already ran demo for instant `-Fast` start
