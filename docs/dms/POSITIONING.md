# DMS POSITIONING & WEDGE
**The answer to "why would anyone switch?" — and what to build so the answer is real.**

---

## 1. Your doubt is correct, and it kills one specific plan

"Normal people who don't buy won't buy; who has bought won't change." That's not pessimism, it's the central law of enterprise software. Switching costs (data, pipelines, trained staff, contracts) make "rip out your system for an unknown startup" the single hardest sell in the market.

So the plan that dies here is **"migrate companies off Salesforce/Snowflake/their WMS onto my platform."** The Malaysian listed logistics players already run WMS, temperature control, real-time tracking, fulfilment systems. There is no reason for them to leave a working system, and "we'll migrate you fast and safe" doesn't create one. Drop this frame entirely. It will also get you torn apart by any sharp investor, because they know switching costs cold.

Everything below is the plan that survives.

---

## 2. Get the competitive map right (you are not fighting Snowflake)

You're conflating different layers. They don't compete with you; most of them sit *underneath* you or never touch your problem.

| Player | What it actually is | Relationship to you |
|---|---|---|
| **Snowflake / Databricks** | Cloud data warehouse / lakehouse — analytics infrastructure | Sits underneath. You read from it; you don't replace it. |
| **Salesforce** | CRM — system of record for customers/sales | Read from it (layer on top). Not a warehouse system. |
| **Oracle / SAP** | Database + ERP — operational system of record | Read from it. Replacing it is a multi-year knife fight; don't. |
| **WMS (incumbent warehouse software)** | Runs storage/picking/inventory | The thing the big players already have. Layer on top, or target who doesn't have it. |
| **respond.io** | Omnichannel customer messaging | This is your **Closer's** neighbour, not your DMS's. |

**The reframe:** your DMS is an *operational vertical system + a governed AI layer*. None of the above does photograph→auto-inventory→slotting→movement. You're doing a thing they don't do. That's the whole point.

---

## 3. The listed players already have systems — so they're "layer" or "logo," not "rip-and-replace"

TASCO runs 25 logistics centres with 2,200 staff and real-time tracking; GDEX warehouses already have WMS + CCTV; Swift Haulage already does warehousing, e-fulfilment, repacking/labelling/palletizing. Two ways to use them:

- **Layer-on-top opportunity:** a governed AI + chat + audit layer over their existing WMS. Non-displacing. Hard to say no to because nothing gets ripped out.
- **Reference logo / distribution:** a 3PL like TASCO or GDEX has *its own customers* (TASCO alone hosts 80+ companies in its facilities). The vision automation could be something a 3PL offers *to its customers* — a B2B2B / embedded angle. One 3PL deal = many downstream warehouses.

Either way: big listed players are not your "migrate them off WMS" targets. They're your credibility and your distribution.

---

## 4. The two wedges that actually work

### Wedge A — Governed AI layer on top (mid-market + listed)
Their system of record stays. Cortex connects (read-only connectors / scrape), adds the chat space, task-prediction, compliance, and tamper-evident audit. "Migration" here = **connectors, not replacement.** Low switching cost. This is the enterprise path and the investable one.

### Wedge B — Greenfield operational system (warehouse/logistics SMEs on Excel/paper)
Nothing to migrate *from* — you are their first real system. The vision automation is the hook that makes a non-buyer become a buyer, because it does something their spreadsheet fundamentally can't. Honest economics: lower deal size, slower, high-touch, fragmented. But it's where your differentiator lands and where forward deployment compounds into a moat. This is where you get your first 1–3 reference clients.

**The honest "why switch" answer, finally:** for Wedge A nobody switches (you're additive). For Wedge B there's nothing to switch from (you're first). In neither case are you asking someone to abandon a working system — which is exactly why it can work.

---

## 5. The killer demo: vision warehouse automation (your strongest idea), built on a feasibility ladder

This is the product highlight. Define it as one sentence: **"Photograph your warehouse and it builds the inventory, tells you where to put things, tracks what moves, and keeps every step governed and audited."** Neither WMS nor Excel does the photo-driven part. That's the demo that makes people lean forward.

**But do not build the moonshot first.** Single-RGB-photo metric dimensioning is unreliable (no scale reference), full 3D auto-mapping is expensive, and a generation model (e.g. Nano Banana) is the wrong tool for *measuring* — you want vision-understanding/detection + a depth or reference source. So stage it:

| Stage | What it does | How (honest) | Sellable? |
|---|---|---|---|
| **V0** *(ship first)* | Structured location map + QR/barcode labels + photo-on-intake + scan-on-move | Engineer defines zones/racks/bins as a location tree; QR per bin; phone photo attached to each item as the record; scan QR on move. **No fancy vision.** | **Yes — already beats Excel.** This is the demo and the first pilot. |
| **V1** | Vision-assisted dimensioning + free-space estimate | Phone LiDAR/depth *or* a known-size reference marker in frame → metric dimensions; volume accounting (bin capacity − occupied) → free space | Yes — first "magic" moment |
| **V2** | "Where to store best" — slotting prediction | Velocity-based slotting + bin-packing heuristic over dimensions + item velocity + bin capacity | Yes — the optimization story |
| **V3** *(frontier, last)* | Vision movement capture + fuller map reconstruction | Object detection at dock/gate; photogrammetry/SLAM for 3D map | The "revolutionary" headline, but hardest. Build only after V0–V2 pay. |

**V0 is the thing you ship and sell soon.** V1–V3 are what make the marketing sound revolutionary — but they layer onto V0's governed data spine (the F1–F7 loop from the build plan). The auto-recorded movement, the captured "skills" (intake_item, slot_item, record_movement), all sit on the same ledger and compliance engine you already have.

---

## 6. What the forward-deployed engineer actually does (the playbook + the stickiness)

This is the moat — services + embedding, not the RAG. Per client, the FDE:

1. **Discovery** — walk the warehouse, learn the actual ops and the messy reality.
2. **Ingest / migrate** — pull from Excel/paper/existing system → clean → entity-resolve → schema-map → load. (AI proposes the rules; deterministic engine executes; human approves. The pattern you already have.)
3. **Physical setup** — define the zone/bin location tree; design and **apply the labeling scheme** (print + stick QR/barcodes); calibrate the photo + scan flow.
4. **Train + educate** — teach staff the new habits: scan-on-move, photo-on-intake, the approval flow. *This is the stickiness.* Once their muscle memory and daily operation run on your system, the painful switch becomes leaving you.
5. **Govern** — set roles, row-level access, compliance rules, audit; hand over the dashboard.

The reason clients don't churn isn't a contract — it's that the FDE rewired their operation around your system. That's why forward deployment beats self-serve SaaS for retention, and it's the honest answer to "what stops them leaving."

---

## 7. Investment positioning (the narrative that survives a sharp investor)

**One-liner:** *"Forward-deployed, sovereign operational AI for warehousing and logistics. We don't replace your data systems — we run the operation on top of them and capture how the work is actually done as governed, auditable skills."*

**Why it's investable:**
- **Real category convergence** — process intelligence + agentic execution is a live, funded wave; you're riding it with a vertical wedge.
- **A differentiated capability** incumbents don't have — vision-driven operational automation (the V-ladder).
- **Sovereign / governed** — fits Malaysian + ASEAN data-residency reality; the data never leaves the box.
- **Forward-deployment moat** — high retention, natural expansion, and a services-funded build.
- **A compounding flywheel** — every client's captured skills + eval corpus make the next deployment faster and better.

**What to show, honestly:**
- One flagship design partner (Wedge B warehouse SME) with V0 live.
- A working vision demo (V0, ideally V1).
- A clear *land-services → expand-platform* motion with real numbers from the pilot.
- **No vanity metrics.** You already know synthetic engagement backfires; sophisticated investors detect it and it poisons the round. The story is a sharp wedge + a real demo + proof you can deploy. That's what pre-seed funds, not a $100B slide.

**The trap to avoid in the pitch:** never lead with "migrate everyone off Salesforce/Snowflake." Lead with "additive layer + greenfield vision wedge." The first gets you challenged on switching costs and you lose; the second is defensible.

---

## 8. Sequencing & focus (hold the line)

- **Closer-into-RUMA is your faster cash.** Property has clear transactions, and "before, people acquired customers by hand" gives an obvious before/after. If you need revenue sooner, that wedge closes faster than warehouse SMEs. Don't starve it for the DMS dream.
- **DMS-vision is the bigger differentiated bet but a meatier build.** Ship **V0 to one warehouse design partner before building V1–V3.** Do not let the vision moonshot delay a paying pilot.
- **One paying pilot on either front beats a perfect platform.** Same conclusion as always: the next signed client is the only metric that compounds.

The vision automation is genuinely your best idea. Build the boring V0 of it first, get one warehouse running on it, and let *that* — not a migration pitch — be the thing you sell and raise on.
