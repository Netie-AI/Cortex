# Cortex

Netie Cortex: governed agentic runtime for warehouse/logistics SMEs.

**Product roles:** Cortex = central brain (MoE / architecture presets / orchestrate). Keys + leave-machine gate + FreeRoute = **OpenVault**. See [`PRODUCT_ROLES.md`](PRODUCT_ROLES.md).

**Whitepaper (architecture · apps · roadmap · branches):** [`docs/strategy/CORTEX_WHITEPAPER.md`](docs/strategy/CORTEX_WHITEPAPER.md)

Open-source agentic AI runtime: install locally, bring your own API key (OpenAI, Anthropic, Mistral, etc.). The system takes a natural-language task, synthesizes a minimal execution DAG, runs it on your compute, and applies Wasm sandboxing + platform security.

## Read first

| File | When |
|------|------|
| [CLAUDE.md](CLAUDE.md) | **Start here.** Invariants, protected paths, how to verify. Any agent or contributor. |
| [STATUS.md](STATUS.md) | Current gate, debt, next feature |
| [docs/PLUG_AND_PLAY.md](docs/PLUG_AND_PLAY.md) | One-call `plug_in(app)` integration |

Older handoff docs (`CURSOR_HANDOFF.md`, `CLAUDE_HANDOFF.md`, `CONTEXT.md`,
`CORTEX_COMPLETE_PLAN.md`) describe a state the repo has moved past — they still
reference F7 and a 153-test suite. They are kept for history; do not start from them.

```powershell
python scripts/handoff.py --cursor   # builder
python scripts/handoff.py --claude   # supervisor
```

## Quick start (demo now)

**New laptop?** Full guide: [docs/SETUP_NEW_MACHINE.md](docs/SETUP_NEW_MACHINE.md)

```powershell
# 1. Clone, copy env.local to repo root, then:
.\demo\run_demo.ps1          # first run (~2 min data build)
.\demo\run_demo.ps1 -Fast    # restart in ~30s
```

- UI: http://localhost:3000
- Warehouse: http://localhost:3000/warehouse
- Chat: http://localhost:3000/chat
- API: http://localhost:8000/health

**Show script:** [docs/DEMO.md](docs/DEMO.md)

### Generated data (not tracked)

`data/dms_demo.duckdb` and `packs/data/dms_ops.db` are runtime stores, rebuilt from
committed source — the CSVs in `data/samples/` and the pack's ontology YAML. A running
engine (and a plain `pytest` run) writes to both, so neither is in git. Nothing needs
them to exist: the code creates them on first use. Point Cortex and DMS Studio at the
same file with `DMS_WAREHOUSE_DB` (absolute path; unset keeps this repo's demo file).
To build them explicitly:

```powershell
python -m CortexOS.dms.warehouse_db   # data/dms_demo.duckdb from data/samples/*.csv
python -m scripts.seed_ops_db         # packs/data/dms_ops.db: schema, ontology, demo bins
```

## Tests

**Broken `myenv` / `.venv_gpu`?** Use the bootstrap venv (Windows, no MSVC/Rust build):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_venv.ps1
.\.venv\Scripts\python.exe -m pytest tests\test_openvault_client.py tests\test_openvault_gate.py tests\test_workflow_runner.py -q
```

Or one shot:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_orchestration_tests.ps1
```

Full suite (needs all optional deps):

```powershell
python -m pytest tests/ -q
```

## Docs

See [docs/README.md](docs/README.md) for build plans, gates, and Cursor governance.
