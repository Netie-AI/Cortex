# VIDEO_YC_PROMPTS — paste-ready gen pack

Use with the Playwright **Record** clips under `video-assets/scripts/`.
Truth table: [`VIDEO_YC_MULTIPLAYER.md`](./VIDEO_YC_MULTIPLAYER.md).

Target: **~60–90s** YC-style multiplayer cut. One job per beat. No purple SaaS tropes.

---

## Master VO (full cut)

> Most AI demos are a single chat window in the cloud.
> We run three local surfaces that stay honest about who does what.
> **AirGPT** is the host shell — where you create and share.
> **OpenVault** holds the keys and the gate — nothing leaves without permission.
> **Cortex** is the brain — orchestration on your machine.
> Share a small app on the company LAN. Join the same live agent thread.
> Bypass? Denied. Secrets? Never in the share.
> Local mesh. Multiplayer agents. Custody that doesn’t lie.

---

## Per-act prompts (paste into image/VO/edit tools)

### Act 1 — Mesh alive
**Visual:** OpenVault `#mesh` peers green + AirGPT header pills OK + Cortex health JSON flash.  
**VO:** “Three surfaces. One mesh. Cortex thinks. OpenVault holds keys. AirGPT is the shell.”  
**On-screen text (max 6 words):** `Local mesh. Three surfaces.`  
**Music:** low pulse, no riser spam.

### Act 2 — Keys SoT
**Visual:** OpenVault Vault tab / keyvault snapshot — configured vs missing, no raw secrets.  
**VO:** “Keys live in OpenVault. The shell only caches offline.”  
**On-screen text:** `Keys source of truth.`  
**B-roll forbid:** pasting API keys in cleartext.

### Act 3 — Create + Share LAN
**Visual:** AirGPT `Create app` → `Share LAN` → share id appears; firewall bypass attempt denied in log.  
**VO:** “Create on your laptop. Share on the LAN — not the public internet.”  
**On-screen text:** `Share code. No secrets.`  

### Act 4 — Cortex brain
**Visual:** Cortex `/health` ok + OpenVault Engine/mesh showing Cortex peer approved.  
**VO:** “Cortex runs the brain locally. No third orchestrator.”  
**On-screen text:** `Brain on your box.`  

### Act 6 — Multiplayer + gate
**Visual:** Session create → join as teammate-2; gate `force` denied; firewall `bypass` denied.  
**VO:** “Join the live thread. Bypass stays denied. Custody that doesn’t lie.”  
**On-screen text:** `Multiplayer. Gate holds.`  
**End card:** `Cortex · OpenVault · AirGPT` + local URLs (no fake metrics).

---

## Thumbnail / still prompts

1. `Three stacked product surfaces on a warm paper desk, AirGPT shell, OpenVault glass console, Cortex health JSON, LAN cable motif, no logos from other brands, editorial product still`
2. `Macro of “bypass denied” toast on dark liquid-glass UI, restrained typography, no neon purple`
3. `Two laptops on one desk sharing one agent thread diagram, LAN-first, documentary lighting`

---

## Edit checklist

- [ ] Acts 1 → 2 → 3 → 4 → 6 only (no act 5)
- [ ] No claim of AWS/Azure multi-tenant cloud
- [ ] No raw secrets on screen
- [ ] Bypass/force shown as **denied**
- [ ] End card lists real localhost ports
- [ ] If AirGPT demo shell used, VO may say “host shell” (not “production AirGPT binary”)
