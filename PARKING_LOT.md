# PARKING_LOT.md
**Deferred ideas. Do not build until condition is met. Add new items here mid-sprint, not to active BUILD_PLAN.**

---

## P1 — Full Palantir ontology + AIP parity
Governed semantic objects, lineage, actions. **Condition:** 1+ paying clients, F1–F7 production-hardened.

## P2 — WASM / Firecracker production hardening
**Condition:** First enterprise client conversation. *(Scaffold: `CortexOS/execution/wasm_isolate.py` — fuel sandbox only.)*

## P3 — DAG token optimization + Temporal durable execution
**Condition:** 100+ DAG runs/day from real clients. *(Partial: Ponytail middleware shipped — see `CortexOS/ponytail/` and `docs/PONYTAIL.md`. S1 watcher agents activate DBOS path per BUILD_PLAN_V2 — governed detect→draft→approve landed; durable resume still open.)*

## P4 — respond.io-style endpoint / Closer auto-reply
WhatsApp/email in, sentiment score, warmed delayed draft (2–5s), human approve before send. **No em dash** in generated replies. **Condition:** DMS paying partner OR explicit RUMA priority. *(Research: `docs/research/respond_io_analysis.md`.)*

## P5 — Full F2–F7 if pilot asks for chat loop before we planned
**Condition:** V0+V1 live + client confirms they want task-suggest chat next. *(Note: F2–F5 now shipped in codebase; pilot confirmation still required before sales promise.)*

## P6 — SQL automation / CSV-Excel ingest pipeline
Folder watch, schema infer, agentic cleaning (AI proposes, deterministic applies), Splink dedup, standard output format.
**Condition:** V1 gated + pilot has dirty imports.
**Research when building:**
- great-expectations/great_expectations
- dbt-labs/dbt-core
- moj-analytical-services/splink
- cleanlab/cleanlab

## P7 — Annotation / labeling pipeline
YOLO / Grounding DINO / Label Studio self-hosted. **Condition:** 50+ real warehouse interactions from pilot.

## P8 — Automated handoff ~~(beyond manual STATUS paste)~~
**SHIPPED 2026-06-26** — `CLAUDE_HANDOFF.md`, `CURSOR_HANDOFF.md`, `python scripts/handoff.py --write`. Keep this entry as historical; do not re-build.

## P9 — Cortex as respond.io replacement (full)
**Condition:** DMS revenue + RUMA Closer validates sentiment draft loop.

## P10 — FDE full playbook
**Condition:** First paid pilot completes — write from what happened.

## P11 — Post-quantum crypto (ML-KEM / ML-DSA)
**Condition:** Regulated-industry client demands it.

## P12 — Company central brain (everyone contributes files) + Spaces
**Condition:** F2–F6 live + 3+ regular users at one client.
**Product lock 2026-07-29:** central chat + **Spaces** (sandbox over selected personal/team/shared sources);
ACL in data plane; Excel source-only; Phase0 Postgres before amend.
`docs/strategy/DMS_SPACES_PRODUCT_2026-07-29.md`
`distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md`

## P13 — Blockchain / Web3 / Talkie / ASA / AIM / NetieX / Vanguard
**Condition:** H2, DMS profitable.

## P14 — Engine-as-SDK / company dual-brain
The whole runtime packaged as an engine + SDK an FDE points at a customer's data/use-case to generate a governed app. "Dual brain" = reasoning/runtime brain (`netie-engine`) + application/ontology brain (`main`), sharing one governance spine. This is the *how* under P1 (ontology parity) and P12 (company central brain). **Plan:** `docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md`. **Condition:** reconciliation option chosen (plan §2) + Ontology O1/O3/O4 shipped. *(The "SDK" is O4 `agents/sdk.py`; the "make the app you want" is O7 `scripts/new_pack.py`.)*

## P15 — netie-engine ↔ main capability landing
`main` and `netie-engine` diverged by 327 files / +34.5k / −5.2k (branched at `dms-v2`). **Do not big-bang merge/rebase** (aborts on add/add conflicts across `registry.py`/`store.py`/engine routes). Land engine capabilities (L0 DuckLake, rawknn/hybrid-RAG memory, Q1/Q2, S0 streams, agent orchestration) into `main` **one gate + one green CI at a time**; pick a canonical home per duplicated core file. **Condition:** plan §2 decision made. *(netie-engine stays the R&D feeder; see plan §2 option C→B.)*
**L0 DuckLake (2026-07-26):** reconciled — lakehouse pack/migrate/test identical across branches; `lakehouse_routes.py` gained only `from __future__ import annotations` from netie-engine. Next capability landing: rawknn/hybrid-RAG memory.

## P16 — Agentic hardening from research
Fold in patterns from the Claude Code Ultimate Guide + Cursor changelog (July 2026): safety hooks (dangerous-action block → injection detect → output-secrets scan), Restrict/Allow/**Request** permission tiers (the SDK's `confirm_required` gate is the Request tier), context-pressure thresholds, agent lifecycle hooks (`beforeSubmitPrompt`/`afterAgentResponse`/`stop`), and an engine session/observability dashboard. **SHIPPED so far:** evals harness (`agent_sdk/evals.py` scope-containment — agent tools/objects/actions must exist in the registry; events aren't invocable — **required gate before O6**); lifecycle hooks + output-secrets scanner (`agent_sdk/hooks.py` — `before_action`/`after_action`/`on_denied` observers, error-swallowing so a bad hook can't break governance, plus a built-in result secret-scan); the SDK `confirm_required` = the Request tier. **Remaining:** safety-hook *veto* semantics, context-pressure thresholds, observability dashboard. **Condition:** remaining slices before tool #2 / before the SDK loads any third-party MCP. *(Detail: plan §4.)*

## P17 — Cortex Engine API layer + self-host packaging (the external product surface)
Two ways for outside builders to consume Cortex (the final goal — `docs/strategy/CORTEX_FINAL_GOAL.md`): **(a) hosted API** to use only the parts they need (orchestration, retrieval, memory, model routing, ontology/action governance, context assembly); **(b) downloadable "netie engine"** to self-host, configure (models/storage/routing/governance), and run against their own data/base. Both hit the same governed core (actions-only write, RBAC + ledger). The Ontology Agent SDK (O4, shipped) is the in-process surface this wraps; the O5 `/dms/sidecar/*` bridge and the context-engineering API (`be945ac`) are the first engine surfaces exposed over the wire. **Condition:** O4 Agent SDK shipped ✓ + first external-consumer ask.

### P17a — OpenVault companion (self-host key/secret + policy-gate service)
`D:\OpenVault` is its own deployable repo (Docker/compose, OpenMW, Profiler, `nvme_sentinel`) — the sovereign **key-vault + gate** service for the self-host path. AirGPT bridges it via `openvault_bridge.py` / `openvault_gate`; the parallel track is standing up `/api/gate/check` + `/api/keyvault/*`. Ship it **with** the downloadable engine so self-hosters get key management + policy gating without trusting a hosted service. **Deploy target when shipping:** everything under `D:\OpenVault`. **Condition:** rides with P17 (self-host packaging).

## P18 — Engine API documentation + whitepaper + architecture reference
Make the engine adoptable **without reading source**: strong per-surface API docs (contracts/examples/auth), the design **whitepaper** (ontology-as-memory + actions-as-only-write-path + dual-brain + hosted/self-host governance parity), and an architecture reference built on the O2 codebase map. First-class deliverables, not afterthoughts — the API *is* the product. **Condition:** P17 API layer has a stable surface to document.
**Shipped (thesis):** `docs/strategy/CORTEX_WHITEPAPER.md` (2026-07-29) — ecosystem diagram, repo map, app contracts (OpenVault/FreeRoute, DMS Spaces, AirGPT, Pointer), roadmap, measured branch truth. **Still open:** per-surface API docs + O2-backed architecture reference booklet.

## P19 — Skill distill continuous learning (Claude Code + Cursor → Netie)
Keep asking Claude Code / Cursor / Claude.app how they implement memory, RAG,
tools/MCP, skills, subagents, one-shot plan, multitask, cloud deploy/scale.
Store under `skill_distill/captures/`; always trace `skill_distill/DISTILL.md`.
Ingest: `python scripts/distill_ingest.py`. Promote survivors to `.cursor/` rules/skills
and Cortex discovery; defer the rest here with `distill:` cites.
**Condition:** ongoing — process after every major orchestration learning.
**Seed:** Claude Capabilities tool lazy-load + Skills/Connectors/Plugins split
(`skill_distill/sources/claude_capabilities_2026-07-24.md`).

## P20 — Rust hot paths for safety + concurrency (not inference kernels)
Rewrite *Cortex* hot paths in Rust where Python is the bottleneck or unsafe:
scoreboard embed + family match, step-journal concurrent writes, zip-slip /
secrets scan for app packages, optional CUDA *probe* helpers. Do **not** rewrite
PagedAttention / RadixAttention / GGUF loaders — those stay in vLLM/SGLang/Ollama;
Cortex remains the WD-40 lubricant (`engine/lubricant.py`). Full gen tok/s bakeoff
(TTFT, throughput) is opt-in once a live model is pulled — today's bakeoff is
reachability+health only.
**Condition:** after Just Works defaults are productized in AirGPT UI + first
measured hotspot (cProfile / py-spy) shows >10% of governed request time in a
listed path.

### P19 open distill debts
- [ ] Optional HTTP bridge (CORTEX_CURSOR_BRIDGE_URL) can drive a local Cursor sidecar later — `distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md`
- [ ] Native click-the-sidebar / open Cursor GUI from the engine process is not available — `distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md`
- [ ] Review capture for deferred items — `distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md`
- [ ] Review capture for deferred items — `distill: skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md`
- [ ] Review capture for deferred items — `distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md`
- [ ] Review capture for deferred items — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md`
- [ ] Review capture for deferred items — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`
- [ ] Review capture for deferred items — `distill: skill_distill/captures/2026-07-27_cursor_rag-authority-planes.md`
- [ ] Observed allowlist includes composer-2.5, composer-2.5-fast, claude-opus-4-8-thinking-high, cursor-grok-4.5-high, gpt-5.6-sol-medium — `distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`
- [ ] Compaction thresholds for Cursor tool traces are product-internal UNKNOWN — `distill: skill_distill/captures/2026-07-25_cursor_distill-session.md`
- [ ] Third-party MCP client remains P16; discovery is recommendation-only — `distill: skill_distill/captures/2026-07-25_cursor_distill-session.md`
- [ ] Cloud agents use separate VM/branch; base must be remote-reachable — `distill: skill_distill/captures/2026-07-25_cursor_distill-session.md`
- [ ] Live Claude Code capture still required for high-confidence multitask/cloud internals — `distill: skill_distill/captures/2026-07-25_claude-code_distill-inferred.md`
- [ ] Claude Code plan→multitask is the UX Netie should emulate with explicit DAG plans — `distill: skill_distill/captures/2026-07-25_claude-code_distill-inferred.md`
- [ ] find-skills / npx skills install path could not be confirmed from official docs this session — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] Cloud tasks run one gVisor VM per task (4vCPU/16GB/30GB), auto-accepted permissions, claude/* branches, connectors-only MCP, setup script cached ~7 days — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] OS sandbox: Seatbelt/bubblewrap, write cwd+TMPDIR, domain allowlist, credential deny/mask needing tlsTerminate — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] Claude Code settings are separate from chat Capabilities — `distill: skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md`
- [ ] Cross-provider memory import is a first-class Claude feature — `distill: skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md`
- [ ] Chat-search memory ≠ legacy generated memory — `distill: skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md`
- [x] Live Claude Code ASK paste — settled `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [x] Cursor model routing / multitask — settled `distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`
- [x] Engine build-now (deferred tools, fire wrap, subagent sanitize, step journal) — all five on the execution path + integration-tested 2026-07-25 — `distill: skill_distill/learned/engine_improvements_from_distill.md`
- [ ] Step-journal retention/pruning (one row per node per run, grows forever) — **Condition:** first long-lived deployment — `distill: skill_distill/learned/engine_improvements_from_distill.md`
- [ ] Task model allowlist drift (composer/frontier slugs) — refresh when harness changes — `distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`
- [ ] Model fallback when blocked (admin/plan/Max Mode) — `distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`
- [ ] find-skills / npx skills path confirmation (E5) — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] Workflow-script parity beyond DAG templates — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] Cloud VM / gVisor parity (P17) — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] OS sandbox + credential mask (OpenVault P17a) — `distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- [ ] Third-party MCP client (P16) — `distill: skill_distill/captures/2026-07-25_cursor_distill-session.md`
- [ ] Split chat-search vs legacy memory stores — `distill: skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md`
- [ ] Cross-provider memory import — `distill: skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md`
- [ ] Cowork / Claude in Chrome differences — `distill: skill_distill/DISTILL.md`
- [x] Anthropic five coordination patterns mapped + generator-verifier loop shipped — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`
- [ ] Agent teams runtime (persistent workers + shared queue + conflict partitioning) — **Condition:** long-running independent batch/migration product need — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`
- [ ] Message-bus production A2A (topics, correlation ids, drop metrics) — **Condition:** event-driven pipeline demand — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`
- [ ] Shared-state multi-writer termination (budget / convergence / adjudicator) — **Condition:** collaborative research surface — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`
- [ ] Wire generator_verifier revise fan-back into workflow_runner (today: verify-once phases) — **Condition:** after GV helper proven on live fires — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`
- [ ] GV production path: AGENT_TASK wrappers + `race_router.eval_predicates` fail-closed (predicate > judge); journal attempts; align with gen_cFSM REGENERATE — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md`
- [ ] Refuse problem-centric planner/coder/tester role-swarm presets (Anthropic anti-pattern) — keep as standing rule — `distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md`
- [ ] **Friday DMS P0** — lakehouse migrate+wire Q&A, provenance API/UI, paraphrase close, xlsx→ask smoke — `distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md`
- [ ] Honesty: never claim MemPalace/Mem0/Qdrant-memory or trained JEPA as shipped — `distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md`
- [ ] Pointer/Clicks demos require `PACK=dms` + live `/dms/secure` — `distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md`

---

## P21 — Enterprise gen-cFSM loop (proactive-first / **active AI** → JEPA → DAG → telemetry → updates)
**Key idea:** actively do stuff toward an ethical enterprise goal — do **not** wait to be asked.
Reactive open-set ingress is secondary. Foundation already shipped (one-sentence routines +
folder→described/gated/dockerized apps with visible guesses; approval + secrets stay manual).
**Active AI build-now:** G2.0 goal schema + G2.1 seeker — packet
`docs/dms/packets/CURSOR_TO_CLAUDE_G2_SEEK_2026-07-26.md`.
**Plan:** `docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md`.
**Already shipped (do not rebuild):** G1.0/G1.1 gen-cFSM, scoreboard JEPA *family* gate,
routines composer, app onboarding/dockerize, AirGPT UI proxies (Cursor).
**Promote by phase when owner gates:** G2.0 goal schema → **G2.1 proactive seeker** →
G2.2 action-value JEPA → G2.3 OSR/reactive ingress → G2.4 telemetry → G2.5 pattern-armed assist →
G2.6 update/OAuth (with P17/P17a).
**Condition:** G2.0/G2.1 after this handoff; silence litmus required for G2.1; G2.6 after
OpenVault update/auth path; never silent auto-update, auto-approve apps, or predicate-free money actions.

---

## P22 — C4.follow + post-T7 C-line queue

**Shipped in working tree (Cortex lane):**

- **C4-min** — submit seam, session bind, pool, JWKS, DuckDB location invariant
- **C6** — `entry_scope ⊆ session_scope` in storage query; Space-keyed anaphora
- **T7-min** — pipeline lineage gate; drill-through rewrite + HMAC token;
  contract **1.2.0** additive Answer fields + `drillthrough` op
- **C10-min** — paraphrase `wrong==0` + robustness floor ratchet

Packets: `CORTEX_TO_DMS_C4_*`, `C6_KICKOFF`, `T7_PROVENANCE`, `C10_KICKOFF`,
plus kickoffs for C5 / C7 / C8 / C9 / C11.

Still open before calling C4 "full":

- Migrate `packs/dms/lakehouse/catalog.py` + `scripts/lakehouse_migrate.py` off
  direct `duckdb` (extend AST invariant beyond `CortexOS/`)
- Close ungoverned agent-SDK reads relative to `enforce_manifest`
- Multi-pool memory broker / write pool / T10 activation

**Next ordered mins (packets only until started):** C5 → C8 → C7 → C11 → C9-full.
OpenVault trust root (`30a8d9a`) already settled.

---

## Move out of parking lot
1. Condition met.
2. Claude gate or explicit decision.
3. Add to `docs/dms/BUILD_PLAN.md`.
4. Update `STATUS.md`.
Never mid-sprint.
