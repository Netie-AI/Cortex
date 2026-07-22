# Run Cortex DMS demo on a new laptop

One-time setup, then one command every time you want the demo.

## Prerequisites (install once)

| Tool | Version | Check |
|------|---------|-------|
| **Git** | any recent | `git --version` |
| **Python** | 3.10+ | `python --version` |
| **Node.js** | 18+ | `node --version` and `npm --version` |

Windows: install [Python](https://www.python.org/downloads/) (check "Add to PATH") and [Node.js LTS](https://nodejs.org/).

## Step 1 — Clone

```powershell
git clone https://github.com/Netie-AI/Cortex.git
cd Cortex
git checkout dms-v2
```

## Step 2 — Copy your secrets file

Copy `env.local` from your main machine into the **repo root** (same folder as `README.md`).

```powershell
# Example: if you copied it to Downloads
Copy-Item "$env:USERPROFILE\Downloads\env.local" .\env.local
```

`env.local` is gitignored — it never goes to GitHub. Use USB, cloud drive, or secure copy.

Not sure what to put in it? Start from the template:

```powershell
Copy-Item env.local.example env.local
# Edit env.local — at minimum set ANTHROPIC_API_KEY for live LLM chat/brain
```

## Step 3 — One command to run the full demo

```powershell
.\demo\run_demo.ps1
```

**First run** (~2 min): installs Python deps, builds 25k-row DuckDB warehouse sample, seeds bins, starts API + UI.

**Later restarts** (~30 s):

```powershell
.\demo\run_demo.ps1 -Fast
```

When you see green `OK` lines, open:

| Page | URL |
|------|-----|
| NL Query | http://localhost:3000 |
| Warehouse | http://localhost:3000/warehouse |
| Chat | http://localhost:3000/chat |
| Skills | http://localhost:3000/skills |
| API health | http://localhost:8000/health |

Show script: [DEMO.md](DEMO.md)

## What `run_demo.ps1` does automatically

1. Loads `env.local` into the process environment (if present)
2. `pip install -e ".[dev,api,dms]"`
3. Generates/cleans warehouse CSVs → DuckDB (skipped in `-Fast` if DB exists)
4. Seeds demo warehouse locations
5. Starts FastAPI on `:8000` and Next.js on `:3000`

## Troubleshooting

```powershell
.\scripts\diagnose_demo.ps1
Get-Content demo\logs\api.err.log -Tail 30
Get-Content demo\logs\ui.err.log -Tail 30
```

| Problem | Fix |
|---------|-----|
| `No Python found` | Install Python 3.10+ and reopen terminal |
| `npm not found` | Install Node.js 18+ |
| Port already in use | Script kills listeners on 8000/3000; or reboot |
| UI blank / 500 | `Remove-Item -Recurse -Force demo\dms-ui\.next`; rerun with `-Fast` |
| Chat returns mock responses | Set `ANTHROPIC_API_KEY` in `env.local` |

## Stop the demo

Close the terminal or kill PIDs shown in the script output. To free ports manually:

```powershell
netstat -ano | findstr ":8000"
netstat -ano | findstr ":3000"
# Stop-Process -Id <PID> -Force
```

## Resume development on the new machine

```powershell
git pull origin dms-v2
python scripts/handoff.py --cursor
pytest -q
```
