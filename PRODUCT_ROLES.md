# PRODUCT_ROLES — Netie surface contract

**Canonical split.** Do not grow a third orchestrator or a second key vault.
Shared across Cortex · OpenVault · AirGPT · OpenIDE · Crew · Constructor · Control. Keep this file identical.

---

## Roles

| Surface | Job | Not its job |
|---------|-----|-------------|
| **Cortex** | Central brain — MoE, pick architecture (DAG / sequential / LangGraph-style / minimal / RAG / memory / computer-control), orchestrate, optimize | Storing keys, one-click deploy UX |
| **OpenVault** | Safe manager + final shipper — where things live, model keys, **FreeRoute** (best-route + budget, OmniRoute-class), one-click connect APIs + local ground models, gating, one-click deploy/host | Running the agent loop itself; picking DAG vs LangGraph |
| **OpenIDE** | Standalone coding app — activates coding expert slice of brain (TSX/canvas, tools, web search, FS, PRs) | Being the host/deploy console |
| **AirGPT** | ChatGPT-layer host shell — phone, settings, pairing, apps hub; thin bridge to Cortex + OpenVault | Owning orchestration forever; second key vault |
| **DMS / Spaces** | GPT-for-database + Excel; Spaces = ACL sandboxes on Cortex lake/query | Being the engine; Pointer Act UI; general ReAct coding agent |
| **Constructor** | Homemade n8n: chat-compiles canvas, ghost dry-run, ontology sketch (Palantir-shaped, not AIP parity) | A second orchestrator; cloning n8n/Activepieces |
| **Cortex Crew** | Grok-bot agentic chat skin (Manager + spawn + A2A) over the engine | Second key vault; implementing tickets in estate cron; LangGraph |
| **Netie Control / Plane** | Display, launch, estate status — fire up apps | Thinking / routing / the agent loop |
| **Netie-KB / skill_distill** | Distill Claude/Cursor skills into Netie | A live tool loop; skipping ingest |
| **Pointer** | External Act / computer-control client -> Cortex engine | DMS Spaces demo surface; custody/deploy |

---

## Safe path (manager + shipper)

```
App (OpenIDE / AirGPT / …)
    → asks Cortex (orchestration only)
        → Cortex plans / MoE / architecture preset
            → OpenVault: resolve where thing lives + keys + gate
                → retrieve / run / deploy
            ← OpenVault ships only what passed gate
        ← Cortex continues under ledger / write gate
```

**Omni-retrieve without OpenVault as gate = unsafe.**
Cortex thinks; OpenVault knows location + keys + “may this leave / deploy?”

---

## Rule of thumb

| Need | Go to |
|------|--------|
| Brains / architecture / MoE / agent loop | **Cortex** |
| Keys / where-is-it / connect / deploy / host / gate | **OpenVault** |
| Code workspace / PRs / FS tools | **OpenIDE** |
| Phone / settings chrome / pairing / apps hub | **AirGPT** |
| Workflow canvas / ontology sketch | **Constructor** |
| Agentic chat / spawn teammates | **Cortex Crew** |
| Launch / status of all local apps | **Netie Control** |
| Distill + skill store | **Netie-KB / skill_distill** |
| Computer-control Act | **Pointer** -> Cortex |

---

## Already half-there (honest)

- **AirGPT** has `/api/openvault/*` + Key Vault UI + OpenFree + `/api/hosting*` — these must stay **thin clients** of OpenVault (and Cortex for engine brains), not a second custody/orchestrator home.
- **OpenVault** today also ships NVMe/mesh/profiler measurement; the **product target** for this contract is keys + gate + connect + deploy/host (baby-easy). Measurement stays adjacent, not a competing product story.
- **Cortex** must MoE-pick architecture presets (DAG vs LangGraph vs minimal vs RAG…). Do **not** add a third orchestrator alongside `dag_runner` / AirGPT queue — extend the surviving Cortex path.

---

## Ownership locks

1. **Keys SoT** = OpenVault encrypted vault (`openmw console` / `/api/keys*`). AirGPT `env.local` is at most an offline cache synced from OpenVault — never a second vault.
2. **Architecture preset SoT** = Cortex (`architecture_preset` on engine config). OpenVault may persist *model slot* preferences (`/api/orchestration/selection`) but does not pick DAG vs LangGraph.
3. **Deploy / leave-machine gate** = OpenVault. Cortex/AirGPT/OpenIDE request; OpenVault allows or denies.
4. **Coding expert activation** = OpenIDE asks Cortex; OpenIDE does not host deploy console UX.
5. **No third orchestrator. No second key vault.**
6. **Crew / Constructor / Control / AirGPT are skins.** They ask Cortex; they do not own the loop, keys, or ship gate.

When in doubt: brains -> Cortex · custody/ship -> OpenVault · code -> OpenIDE · shell -> AirGPT · chat-spawn -> Crew · canvas -> Constructor · launch -> Control.

Agentic-loop gap vs Cursor/Claude (plan, not shipped): `docs/strategy/AGENTIC_LOOP_CAPABILITY_PRD_2026-08-25.md`.
