# Plug-and-play DMS Brain

## 30-second integration

```python
from fastapi import FastAPI
from packs.dms import plug_in, secure_message, classify_message

app = FastAPI()
meta = plug_in(app)  # registers warehouse, chat, query routes
# meta = {"pack": "dms", "routes_registered": [...], ...}
```

```powershell
pip install -e ".[dev,api,dms]"
$env:PACK = "dms"
python -m uvicorn CortexOS.api.main:app --port 8000
```

## Security-first message pipeline

Every inbound message should pass through the harness before any LLM:

```python
from packs.dms import secure_message, classify_message

raw = "URGENT wire transfer to new bank account"
gate = secure_message(raw, block_scam=True)
if gate["blocked"]:
    # steward handoff — never reach model
    ...
else:
    cls = classify_message(gate["safe_text"])
    # cls["psychological_state"] → persona routing
```

## Why safer than generic agent frameworks

| Layer | DMS Brain | Typical agent (OpenClaw-style) |
|---|---|---|
| Pre-model gate | PII + injection + scam harness | Often none |
| Audit | Hash-chained F1 ledger | Optional logs |
| SQL | sqlglot guardrail, SELECT-only | Raw LLM SQL |
| Vision | Suggest-only, human confirm | Auto-commit risk |
| Data | Sovereign on-box default | Cloud default |

## Ponytail token discipline

See [PONYTAIL.md](PONYTAIL.md). Default: YAGNI / stdlib-first before adding deps.

## GPU local inference (optional)

```powershell
.\scripts\setup_gpu_env.ps1
$env:CORTEX_LOCAL_INFERENCE = "1"
python scripts/finetune_dms_tone.py --smoke   # LoRA on warehouse tone corpus
```

## Cross-laptop resume

```powershell
git pull origin main
pytest -q
```
