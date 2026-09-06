# Digital clone -- company brain, Crew capture, PaaS

**Status:** DRAFT -- not law until founder amends `NETIE.md` / TAS.  
**Date:** 2026-09-04  
**Audience:** founder, PRD Agent, Cortex/Crew/Control writers  
**Companions:** [`CORTEX_WHITEPAPER.md`](CORTEX_WHITEPAPER.md) (engine thesis) · [`CORTEX_FINAL_GOAL.md`](CORTEX_FINAL_GOAL.md) · [`NETIE_CORTEX_MASTER_PLAN.md`](NETIE_CORTEX_MASTER_PLAN.md) · Netie [`WP-001`](../../../Netie/White%20Paper%20-%20Why/WP-001-accountable-ai-operating-system.md) · [`WP-003`](../../../Netie/White%20Paper%20-%20Why/WP-003-reuse-the-estate-do-not-rebuild.md) · TAS analog map `D:\Netie\TAS\README.md`

> Do not present planned work as shipped. Shipped vs next is marked in section 8.

---

## 0. One line

Netie becomes the **governed digital operating system of a company**: Cortex is the brain, Crew is how people and agents work (and how working-style is captured), Control is the operator desk, OpenVault is keys. The product is a **consented company clone** -- files, decisions, roles, and employee working-styles -- sold as **platform (engine) plus SaaS (desk + capture)**. Zero-human ops is the horizon: agents execute gated actions; humans remain the accountable owners.

That is not a second Palantir, not a second n8n, and not a silent surveillance product.

---

## 1. Press-release thesis

Every company already has a brain. It lives in people's heads, Slack threads, Excel, and the way one steward closes a ticket. When they leave, the company forgets. When they stay, the company cannot scale that person's judgment.

Netie records **how work actually happens** while the crew is already using the stack:

1. Cortex governs reads and writes (manifest, ledger, ontology actions).
2. Crew is the shared live session where people and agents talk, remember facts, and close GitHub tickets.
3. Control shows the fleet. It does not spawn a second orchestrator.
4. Over time, each consented employee has a **working-style profile** (not a deepfake): how they review, what they refuse, which files they touch, which skills they load.
5. The company has a **digital twin of operations**: objects, links, actions, documents, and the clone roster.
6. Tenants on the hosted engine generate that corpus. That corpus is the moat -- not a pasted analog UI.

**Category claim (honest):** we are not the first to say "digital twin" or "digital worker." Palantir, Microsoft Copilot Studio, Adept-class agents, and internal "skill capture" tools exist. The claim we can defend is: **the first SME-priced, ledger-identical, HITL-fail-closed clone that is captured from the same desk the company already works on** -- Crew + Cortex + Control -- not a six-month Foundry implementation.

**Ultimate goal, restated so it cannot be misread:**

- **Zero routine human ops** for work the ledger already knows how to gate.
- **Never zero accountability.** A person owns every commit, every ship, every spend. Agents do not become co-authors of the company.
- **Never silent clone.** Consent, purpose limitation, PDPA, employment contract. No keylogger, no clipboard steal, no personal inbox scrape.

---

## 2. Founder analog override -- what "lift the bans" actually means

You asked to treat `D:\my*` / `D:\Cortex\my*` as **your previously built projects** and to extract snippets as core features.

Two different facts are both true:

1. **Those folders are on your laptop because you cloned or rebuilt them.** They are the estate. Writers must **read them and port the job** into live Netie products. The old "do not look, analog is frozen forever" posture is too weak for a clone/PaaS push. **DISTILL is now the default**, not a last resort.
2. **A clone on disk is not a license grant.** Several trees are other people's code. Pasting them into Cortex (Apache/MIT product surface) can force AGPL on the engine, block SaaS under Fair-code, or ship copyrighted material. That would kill the dominance path, not help it.

### 2.1 Three buckets (this is the lifted policy)

| Bucket | Meaning | Example | What writers do |
|---|---|---|---|
| **OURS** | Original Netie modules you wrote, or MIT/Apache you may relicense as original Netie | `CortexOS/crew`, gastown-inspired **original** checkpoint code, DeepAgents MIT **pattern** already in `memory.py` | Copy, extract, make it a core feature. Prefer the live file; if the analog still has the better slice, port it as original Netie. |
| **DISTILL** | Study clone of a third-party product; license allows reading; we reimplement | Graphiti temporal ideas, Rakazo memory **contract**, Orca host patterns, Letta/Mem0 **shapes** | Read the analog file. Write original code in the TAS live product. Do not vendor the tree into `CortexOS/`. |
| **BAN COPY** | License or copyright forbids shipping inside Cortex SaaS | Guaca **AGPL-3.0** (`D:\Cortex\myguaca`), OpenWillow **GPLv3**, n8n **Sustainable Use**, leaked Claude Code / system-prompt dumps, grok-bot reconstruction, OpenWorker `ee/`, NVIDIA llm-router, Activepieces 665 pieces as an engine | Study UX. Write original. **Paste zero files.** `GET /stolen.css` stays 410. |

**Lifted:** SKIP/PARK that meant "do not even distill MIT analogs into Crew/Control/Cortex." Those jobs become **core features** on the live TAS product.

**Not lifted:** BAN COPY. Founder ownership of a *folder* does not rewrite Guaca AGPL or n8n Fair-code.

### 2.2 Analog -> live product (core feature map)

Port into the named live module. Do not open a second product.

| Analog tree | Core feature we keep | Live write-target |
|---|---|---|
| gastown / openworker / deepagents | long-horizon, HITL lease, named markdown memory | `D:\Cortex\CortexOS\crew` |
| paperclip | operator board, heartbeat labels | `D:\NetieControl` (display only; 405 on `/v1/run`) |
| OmniRoute | FreeRoute fallback + budget | `D:\OpenVault` |
| Graphiti / Zep / Mem0 / Mempalace / Letta | temporal facts, collections, recall | Cortex `/api/memory` + Crew `facts.md` (no second memory product) |
| Activepieces / n8n | **ideas only** -- canvas compiles, Cortex runs | `D:\Constructor` + Cortex constructor routes |
| Windows-MCP | tool catalog | Pointer encyclopedia; UACC is the mouse |
| Orca / OpenCode | host + TUI patterns | AirGPT / OpenIDE |
| Cogitorium / Semantica | objects+links+actions **UX** | Constructor inspect + Cortex ontology. **P1 Foundry-as-a-service stays parked** |
| Guaca / Rakazo | chrome DNA already replaced | original `crew.css`; Rakazo memory contract already DISTILLed |
| mybot / grok-bot | spawn/kill/idle/goal **behavior** | Crew runtime + OpenVault route. **COPY none of grok-bot source** |
| Claude Code dumps | `/` `@` UX ideas | original Crew slashes. **COPY none of leaked trees** |

### 2.3 Proposed TAS paste (replace the BAN paragraph after founder sign-off)

```text
Read D:\Netie\TAS\README.md analog map and D:\Cortex\docs\strategy\DIGITAL_CLONE_WHITEPAPER.md.
Name the TAS lane and the live files that already own this surface.
DISTILL analog segments into original Netie code in that live product.
OURS/MIT: extract as core features. BAN COPY still: Guaca AGPL, OpenWillow GPL,
n8n Sustainable Use, leaked Claude Code, grok-bot source, OpenWorker ee/,
Activepieces 665 as an engine. Ontology stays Cortex. P1 parked as a product.
Control does not spawn (F-0030). POST /v1/run stays 405.
Write-target for Crew is D:\Cortex\CortexOS\crew -- not D:\Cortex-crew.
```

Until `NETIE.md` / TAS is amended, existing BAN paste in TAS still wins for other agents. This file is the working thesis.

---

## 3. The stack -- build from what already exists

Four planes. One engine. No fifth brain.

```
People + agents
    -> Crew :8020   converse, facts.md, belt, A2A, HITL
    -> Cortex :8000/:8010   think, manifest, ledger, memory, ontology actions
    -> OpenVault :5000   keys, FreeRoute, leave-machine gate
    -> Control :8040   display board/belt/pickup; 405 on run/goal/secrets
    -> DMS / Constructor / Pointer / AirGPT   consumer apps
```

### 3.1 Cortex (the brain)

Already the product we sell to builders (hosted API + self-host "netie engine"):

- Ontology-as-memory + LLM-as-reasoner + **actions as the only write path**
- F1 ledger, F5 compliance, F7 RBAC -- identical for human and agent
- `/api/memory`, `/api/context`, constructor compile, duckdb only under `CortexOS/execution/`
- Ticket Runner seats GitHub writers. Crew is not a second orchestrator.

Clone/PaaS work that belongs **in the engine**: Person/Role/Skill/WorkingStyle as ontology kinds; memory collections per tenant+person; ledger events for "profile updated"; RBAC so a clone cannot read another tenant.

Clone work that does **not** belong in the engine: a new chat product, a new workflow runtime, a Foundry clone.

### 3.2 Crew (the capture surface)

Write-target: **`D:\Cortex\CortexOS\crew`**. Process: `python -m CortexOS.crew` default `:8020`.

Crew is where the company works, so Crew is where the clone is **observed**:

- Spaces, transcripts, `/remember` `/recall` `/forget`, `facts.md` (implemented on `cursor/crew-facts-md-116`, not yet the only live `:8020`)
- Belt + wakes for Control to display
- A2A mailboxes, HITL confirms, skill packs
- Engine only via HTTP. No `packs.*`. No duckdb.

**Do not grow `D:\Cortex-crew`.** TAS-CREW already says it is not the write-target. See section 7.

### 3.3 Control (the desk)

`D:\NetieControl` `:8040`. Fetch/display: `/v1/board` `/v1/belt` `/v1/pickup`. **405** on `/v1/goal` `/v1/run` `/v1/secrets`.

Control shows clone roster health the same way it shows belt/HITL. It never spawns agents (F-0030).

### 3.4 The rest of the ecosystem (do not rebuild)

| Product | Job in the clone story |
|---|---|
| OpenVault | Keys never enter prompts or clone files |
| DMS | Steward chat + warehouse proof the engine works |
| Constructor | Canvas compiles to Cortex actions -- the company's playbooks as graphs |
| Pointer / UACC | Computer control with confirm -- not silent screen capture for clones |
| AirGPT / OpenIDE | Coding expert consumer |
| Space | Desktop consumer |
| GitHub Issues | Ticket bus. Ticket Runner until-goal. Beads/Mayor refused |

---

## 4. Company brain and employee clones

### 4.1 What a clone is

A **digital clone** in this product is:

- a **role** (what the seat is for),
- a **working-style** (how this person actually works: review bar, writing tone, tools they reach for, hours, refusal patterns),
- a **skill set** (Crew skills + Constructor graphs they run),
- a **document/file map** (what they own in the space jail + git),
- **decision traces** (ledger ids, not prompt bodies in telemetry).

It is **not**:

- a voice/face deepfake,
- an unsupervised keylogger,
- a copy of their personal Gmail,
- a bypass of HITL, manifest, or OpenVault,
- a second agent runtime named after the person.

When Crew talks to "the clone of Alice," it loads Alice's **consented profile** as **untrusted working-style context** (same wrap as `CrewMemory.recall`). The system prompt and the manifest still win. A stored sentence is data, never an order.

### 4.2 Data model (files first, ontology second)

YAGNI: do not open duckdb in Crew. Do not invent a second graph database. Reuse Crew memory + Cortex `/api/memory` + ontology kinds.

Per tenant, per space (or a dedicated `company` space):

```
memory/
  INDEX.md                 # roster of fact names (already shipped in CrewMemory)
  facts.md                 # rebuilt bundle (already shipped)
  company.charter.md       # what this company does
  company.org.md           # teams and seats
  person.{id}.profile.md   # role, team, consent_at, retention
  person.{id}.style.md     # working style
  person.{id}.skills.md    # skills this seat uses
```

Caps already exist (`MAX_FACTS`, `MAX_BODY_BYTES`). Clone bodies stay inside those caps or they go to Cortex memory collections, not an unbounded Crew folder.

Later (engine, not Crew sqlite):

- Ontology kinds: `Person`, `Seat`, `Skill`, `WorkingStyle`, `Decision`
- Links: Person-Seat-Team, Person-Skill, Decision-LedgerEvent
- Actions: `clone.update_style` (HITL), `clone.export` (steward), `clone.retire` (GDPR/PDPA delete)

### 4.3 How data is gathered (the flywheel)

While everyone uses Netie Crew:

1. **Explicit remember** -- `/remember` and Save fact. Highest trust.
2. **Work artifacts they already created** -- PRs, issues, Constructor compiles, DMS answers, ticket claims. Org-visible. Purpose: operate the company.
3. **Session summaries** -- opt-in per space. Written as facts, untrusted-wrapped.
4. **Style compiler** -- a Crew skill that reads consented artifacts and proposes a `person.{id}.style.md` **diff** for HITL accept. Never silent overwrite.

What we do **not** gather:

- Secrets (OpenVault).
- Analog cookies / machine Chrome sign-ins.
- Personal devices, webcam, clipboard, keystrokes.
- Other tenants.

**Employment / PDPA:**

- Onboarding: written purpose ("operate and back up how you work in this company").
- Separate consent for "train a seat-clone that can act after you leave."
- Retention + delete (`clone.retire`) as a first-class action.
- Malaysia PDPA + any customer jurisdiction. Default fail-closed: no consent, no style file.

### 4.4 Company clone (the digital brain)

The company clone is the **union** of:

- ontology (what objects exist),
- Constructor graphs (how work is done),
- Crew facts (what the crew learned),
- ledger (what actually happened),
- clone roster (who does it which way).

File structure and documents are first-class because Crew already jails a workspace per space. Do not build a parallel DMS for this. Put company docs in the space workspace + remember names in `INDEX.md`.

### 4.5 Scale thesis

Every seated employee using Crew produces facts and traces. Tenants on PaaS do the same inside their tenancy. **We do not siphon tenant clone data into a Netie-wide model** unless a separate, written training addendum says so. Default: tenant data stays tenant data. That is how you become the operator of companies, not a scandal.

The "first company to clone everyone" pitch to a buyer is: **we clone *your* company for *you*.** Cross-tenant training is a later, optional, contracted product -- not the default of Wave 1.

---

## 5. Zero-human operations

Horizon (H3 in `NETIE_CORTEX_MASTER_PLAN.md`), not Wave 1.

| Humans still do | Agents may do after gates |
|---|---|
| Own the commit, the spend, the ship | Draft, test, open PR, run gated actions |
| HITL on computer control, leave-machine, reviewer floors | Belt work, wakes, A2A, Ticket Runner seats |
| Consent and retire clones | Propose style diffs, recall facts |
| Bind the enterprise goal \(g\) | Seeker toward \(g\) when headroom exists |

**Kill list for this thesis:** autonomous send-money, autonomous production deploy without ship_gate, silent clone training, Control spawning, Beads as a second ticket SoT, a Palantir product unpark, n8n as Constructor engine.

---

## 6. Offer -- platform, SaaS, dominance

Two modes already in `CORTEX_FINAL_GOAL.md` (P17):

1. **Platform (PaaS / engine):** hosted Cortex API + self-host netie engine. Builders compose memory, ontology, orchestration, ledger. DMS pins `cortex-contract`. This is how we become infrastructure.
2. **SaaS (desk + capture):** Crew + Control + clone roster + Ticket Runner, sold to operators who do not want to be engine integrators. This is how we get daily use and the flywheel.

**Do not merge the two into one confused SKU.** Engine customers must not be forced to take Crew chrome. SaaS customers must not have to read `CortexOS/`.

Pricing sketch (not a quote):

- Engine: usage + tenancy (hosted) or license (self-host).
- SaaS seats: per consented clone seat + HITL operator seats.
- FDE in H1 (`NETIE_CORTEX_MASTER_PLAN.md`) until the clone onboard is a wizard.

Dominance path:

1. Be the system of record for **governed actions** in a vertical (warehouse/DMS already proves this).
2. Be the **shared live session** (Crew) so capture is free.
3. Be the **clone export** the next hire reads on day one.
4. Hosted tenancy so every new company adds isolated corpus.
5. Category: accountable operating system -- WP-001 -- not "another agent chat."

---

## 7. Repo merge and analog close

### 7.1 Crew: one tree

| Tree | Decision |
|---|---|
| `D:\Cortex\CortexOS\crew` | **Source of truth.** All Crew features land here. |
| `D:\Cortex-crew` | **Do not grow.** Empty/broken checkout. After unique files (if any) are ported, stop using it. Optional: delete the folder once `git status` is empty of Crew work. |
| `D:\Netie-Crew` | Seed conveyor `:8022` only. Do not become a second engine. |
| Live `:8020` | May be a stale fork. Restart via `scripts/start_crew.ps1` when you are at the keyboard. Do not kill from an agent (R-0015). |

Merge later means: port leftover files from Cortex-crew into CortexOS/crew with tests, then retire Cortex-crew. It does **not** mean git-subtree analog products into Cortex.

### 7.2 Analog trees on `D:\`

| Action | Do | Do not |
|---|---|---|
| Local `D:\my*` / `D:\Cortex\my*` | Keep as DISTILL sources until a license inventory says otherwise | `git add` them into Netie-AI/Cortex |
| Public GitHub **forks** of Guaca, n8n, Claude Code, etc. | Make **private** or **delete the fork** (you do not own upstream) | Delete upstream. Do not republish leaked trees |
| Netie-AI product repos | Keep | Close Cortex, OpenVault, Control, DMS |
| License inventory | One `LICENSE` check per analog root; record OURS / DISTILL / BAN COPY | Assume "I cloned it so I own it" |

**Close analog repos?** Only **your forks** of third-party projects, and only after you decide you no longer need GitHub as a backup of the study tree. Keep the local folder until DISTILL for that lane is done. Do not close Netie product remotes.

### 7.3 Palantir P1

Stay parked **as a product**. The clone thesis uses Cortex ontology + Crew facts + Control display. Unparking Foundry-as-a-service is a separate founder call, not implied by this paper.

---

## 8. Execute -- waves

Stop at the first wave that is not done. Do not open Wave 4 SKUs before Wave 0 is on `main`.

### Wave 0 -- one Crew (this week)

- Land `facts.md` + HUD + tests from `cursor/crew-facts-md-116` onto GitHub `main` (explicit paths, not `git add -A`).
- Restart live `:8020` from CortexOS/crew when you are present.
- Control still displays belt; 405 unchanged.
- License inventory spreadsheet: analog root -> LICENSE -> bucket.
- **Verify:** `python -m pytest tests/test_crew -q` (182+). `ruff` on touched files. GET `/crew/health` after restart.

### Wave 1 -- capture schema (Crew)

- Fact name conventions: `person.{id}.*`, `company.*`.
- `/remember` help text + HUD labels for those names.
- Consent fact required before `person.{id}.style` may be written.
- **Verify:** tests that jail + caps still hold; missing consent refuses style write with a named reason (R-0011).

### Wave 2 -- engine kinds (Cortex)

- Ontology kinds Person/Seat/WorkingStyle (additive contract **minor** if a consumer field is added -- bump `version.py` + `pyproject.toml` + regenerate OpenAPI in the same commit).
- Cortex `/api/memory` collection per tenant+person. Crew remains files; engine holds the durable clone.
- Ledger event on style accept.
- **Verify:** contract tests, no `CortexOS` import of `packs.*`, duckdb still execution-only.

### Wave 3 -- Control desk

- Board card: clone roster as **display** of Crew/Cortex JSON. No POST spawn.
- Pickup/belt already exist; add clone health fields if missing.
- **Verify:** 405 on `/v1/run` `/v1/goal` `/v1/secrets` still tested.

### Wave 4 -- PaaS tenancy

- Hosted Cortex multi-tenant isolation (already implied by F7). Clone collections keyed by tenant.
- SaaS SKU: Crew+Control+clone, engine optional.
- Training-addendum flag default off.
- **Verify:** tenant A recall cannot see tenant B facts.

### Wave 5 -- FDE then self-serve

- One paying company on consented clones (H1 warehouse path still valid as proof).
- Then wizard onboard. Dominance is distribution + corpus inside tenancy, not analog paste.

### Wave 6 -- zero routine ops (H3)

- Seeker + gen-cFSM toward bound goal \(g\).
- Clones as seats on the belt, still HITL for ship/spend/computer.
- Dual-brain plan stays the how: [`ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md`](ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md).

---

## 9. What this paper refuses

- Second orchestrator in Control or Crew `dag_runner`.
- Weakening `CortexOS/execution/manifest.py` refusals.
- Vendoring analog git histories into Cortex.
- Cross-tenant training by default.
- Calling a chat log a "clone" without consent and a style HITL.
- Unparking P1 because "digital brain" sounds like Foundry.

---

## 10. North-star tests

1. **Engine test:** does this change make Cortex a better governed engine, or is it another app? Apps stay consumers.
2. **Capture test:** would this data exist if the employee had not consented to clone-the-seat?
3. **License test:** could we ship this file if Guaca/n8n/Anthropic asked a lawyer? If no, DISTILL or drop.
4. **Desk test:** can Control show it without POSTing run/goal/secrets?
5. **Accountability test:** is a named human still the owner of the commit?

---

## 11. Next human decisions (blocker list)

1. Amend TAS / `NETIE.md` with section 2.3 paste, or keep old BAN paste (other agents will follow TAS).
2. Say yes to land facts.md PR (Wave 0) from the crew-facts worktree.
3. Analog GitHub forks: private vs delete vs keep public (recommendation: private).
4. `D:\Cortex-crew`: confirm delete-after-port (recommendation: yes, after Wave 0).
5. Training-addendum default: **off** (recommendation: keep off).
