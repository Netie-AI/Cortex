# Demo video assets (YC multiplayer cut)

| Path | Role |
|------|------|
| `scripts/record_act1_mesh_alive.py` | Act 1 — three surfaces alive |
| `scripts/record_act2_vault_keys.py` | Act 2 — OpenVault keys SoT |
| `scripts/record_act3_share_lan.py` | Act 3 — Create app + Share LAN |
| `scripts/record_act4_cortex_brain.py` | Act 4 — Cortex brain |
| `scripts/record_act6_multiplayer_gate.py` | Act 6 — multiplayer + deny bypass |
| `scripts/record_all.py` | Run all + ffmpeg concat |
| `out/yc_multiplayer_demo.mp4` | Final cut (generated) |

Truth + VO pack:

- `docs/VIDEO_YC_MULTIPLAYER.md`
- `docs/VIDEO_YC_PROMPTS.md`

## Prerequisites

```bash
bash scripts/start_demo_stack.sh   # Cortex :8000 + OpenVault :5000 + AirGPT :8765
source .venv/bin/activate
python -m playwright install chromium
python video-assets/scripts/record_all.py
```
