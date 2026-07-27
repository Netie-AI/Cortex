# VIDEO_YC_MULTIPLAYER — truth table for the demo film

> Canonical claims for the YC / multiplayer cut. Anything in the video that
> contradicts this table is a fabrication — fix the cut, not the product.

Last reconciled: 2026-07-27 · Surfaces: Cortex `:8000` · OpenVault `:5000` · AirGPT/OpenIDE `:8765`

---

## Product roles (do not blur on camera)

| Surface | Job on camera | Not its job |
|---------|---------------|-------------|
| **AirGPT** | Host shell — create/share app, OpenIDE chrome, join session | Holding raw keys forever / owning orchestration |
| **OpenVault** | Keys SoT, LAN share registry, firewall, deploy gate, mesh | Running the agent loop |
| **Cortex** | Brains — health, pack, orchestration / agent SDK | Storing keys or one-click host UX |

Contract: `PRODUCT_ROLES.md` (identical across repos).

---

## Port + health truth

| Peer | URL | Health probe | Demo-safe claim |
|------|-----|--------------|-----------------|
| OpenVault | `http://127.0.0.1:5000` | `GET /api/healthz` → `status=ok` | Local control plane |
| Cortex | `http://127.0.0.1:8000` | `GET /health` → `status=ok` (+ pack) | Local engine |
| AirGPT / OpenIDE | `http://127.0.0.1:8765` | `GET /` or `/api/healthz` | Shell; real AirGPT preferred, demo shell OK if labeled |

OpenIDE is **not** `:5100` (legacy stub only). Connect pack must pin `:8765`.

---

## Act map ↔ recorder scripts

| Act | Beat | Recorder | Must show | Must not claim |
|-----|------|----------|-----------|----------------|
| **1** | Three surfaces alive | `record_act1_mesh_alive.py` | Mesh tab + AirGPT shell + Cortex health | “Cloud SaaS” / public internet share |
| **2** | Keys SoT | `record_act2_vault_keys.py` | OpenVault Vault tab / keyvault snapshot | AirGPT as second vault |
| **3** | Create + Share LAN | `record_act3_share_lan.py` | Create app + Share LAN; share id issued | Secrets in share payload |
| **4** | Cortex brain | `record_act4_cortex_brain.py` | Cortex `/health` + OpenVault Engine/mesh cortex peer | Third orchestrator |
| **5** | *(skipped in this cut)* | — | — | — |
| **6** | Multiplayer + deny bypass | `record_act6_multiplayer_gate.py` | Session create/join + `bypass`/`force` **denied** | Silent allow on bypass |

---

## Security claims (hard)

1. Private LAN / loopback only — no public internet share in the film.
2. Secrets never leave the vault; share metadata is title/path/owner only.
3. `bypass` / `force` / `skip_rules` → **WARN + deny** (cannot).
4. Deploy / leave-machine still requires `/api/gate/check`.
5. Keys SoT = OpenVault; AirGPT `env.local` = offline cache at most.

---

## Honest environment notes

| Fact | Status in this cloud agent |
|------|----------------------------|
| Cortex + OpenVault repos | Present (`/agent/repos/{cortex,openvault}`) |
| Real AirGPT (`D:\\AirGPT`) | **Not on GitHub** — use `scripts/airgpt_demo_shell.py` stand-in, or mount the real app |
| LLM live keys | Optional; demo works in mock mode without `ANTHROPIC_API_KEY` |
| Gen pack | `docs/VIDEO_YC_PROMPTS.md` |

---

## Bring-up

```bash
# From Cortex checkout (sibling OpenVault expected)
bash scripts/start_demo_stack.sh

# Recorders (Playwright video → video-assets/out/)
python video-assets/scripts/record_all.py
```
