# Next lane prompts — always continue (Cursor ↔ Claude)

**Updated:** 2026-08-22 after Cortex#14 warehouse path  
**Rule:** After every hand-back, both lanes leave the *next* prompt written here and in STATUS.

## Cortex#14 follow — DMS must set the same env (owner: DMS)

```text
Cortex now resolves the serving DuckDB at call time via DMS_WAREHOUSE_DB
(CortexOS.execution.warehouse.warehouse_path). Unset = <Cortex>/data/dms_demo.duckdb
(demo grant). Studio ingest on the DMS product home must set the same absolute
path or chat will keep answering a different file. Do not add MSSQL live
federation (EPIC-020 extract-only).
```


---

## DMS product lock — Spaces / ChatGPT-for-Excel (2026-07-29)

**Pointer = external Act app.** Demo focus = Data Management Service: warehouse AI agents on Excel/DBs.

**Product home:** `D:\DMS` (spin-off). See `D:\DMS\AGENTS.md` and `D:\Cortex\demo\DMS_PRODUCT_HOME.md`.

```text
Read docs/strategy/DMS_SPACES_PRODUCT_2026-07-29.md (binding) and D:\DMS\docs\*.

Build order: Phase0 Postgres ops+ledger+RLS → amend Proposal loop →
schema-retrieval validation gate → lineage → DuckLake catalog Postgres / MinIO →
packaging. Spaces MVP (scoped sandbox Q&A) parallel once ACL exists.

Do not: Excel write-back; claim thousands of connectors; design for 100TB analytics;
compete as cloud lakehouse. Keep 0 confidently wrong.

distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md
```

---

## Friday priority — DMS retrieval (owner: Cursor)

**Status 2026-07-29:** Lakehouse seeded; `sync_warehouse_from_silver` + `POST /dms/lakehouse/sync-warehouse`;
Studio UI at `/studio` (catalog / ingest / pipelines); `DMSQueryResponse` provenance on wire;
Query UI shows badge/layer/suggestions. Do **not** claim MemPalace/Mem0/trained JEPA.

```text
Done this week:
1) lakehouse_migrate (+ warehouse sync from lake.silver → dms_demo.duckdb)
2) DMSQueryResponse + demo/dms-ui provenance (layer, badge, suggestions)
3) vocabulary paraphrase rules; re-run `python -m bench.paraphrase`
4) Studio: upload xlsx → bronze; pipelines promote; SYNC → Q2

Still out: MinIO/500GB object store; DMS_L2 LLM SQL; JEPA/MemPalace.
Next product track: DMS Spaces (see block above).
distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md
```

---

## Contracts settled (safe for UI)

### G2.1–G2.2 seek / learning

| Method | Path | Contract |
|---|---|---|
| POST | `/api/engine/seek` | `proposals[]` with `value`, `relevance`, `value_learned`, `value_n`, `value_why`; `audit: {ok, seq}` |
| POST | `/api/goals/{id}/outcome` | `{proposal_id, outcome}` ∈ `accepted\|succeeded\|dismissed\|failed` |
| GET | `/api/goals/{id}/values` | learned value table |
| GET | `/api/goals/{id}/seeks` | seek history |

If `audit.ok === false` → UI must say **Not audited** (never imply the run was recorded).

### Cursor Seek UI (2026-07-27)

- Shows `value_why` (not bare scores); learned chip when `value_learned`
- Accept / Dismiss → outcome; Accept opens routine draft preview
- Seek history panel; audit warning wired

---

### G2.3 OSR (2026-07-27) — **SHIPPED**

| Method | Path | Contract |
|---|---|---|
| POST | `/api/engine/osr` | `{text, wrapped?}` → `{band, family_id, similarity, novelty_score, proposed_horizon, winner, schema, new_shape, assumptions[]}` |
| POST | `/api/routines/{id}/fire` | now also returns `osr: {band, assumptions[], …}`; `wrapped` stays `true` |

Bands: `known` (proven winner reused) · `near` (top-3 race) · `open` (gen-cFSM, horizon by novelty).
`wrapped: true` on `/api/engine/osr` means the text is **already** untrusted-wrapped; sending raw
text with that flag is refused (`payload_not_wrapped`, 400).

---

### G2.4 telemetry (2026-07-27) — **SHIPPED**

| Method | Path | Contract |
|---|---|---|
| GET | `/api/engine/telemetry?goal_id=&limit=` | `{summary, events[], daily[]}` — numbers only, no prose |
| POST | `/api/engine/telemetry/compact` | rolls raw events → per-day aggregates |

`value_why` now names the evidence source ("3 from your decisions, 12 from runs"). Proposals may
also carry `inferred_capped: true` — that means the engine's own runs disagreed with the user and
were held back. **UI: if you surface that, phrase it as "your decision is being respected", not as
a warning.** Explicit weight 1.0 vs inferred 0.25, and inferred is capped at the explicit weight.

---

### G2.5 forget-recovery (2026-07-27) — **SHIPPED**

| Method | Path | Contract |
|---|---|---|
| GET | `/api/commitments?status=open` | `{commitments[]}` each with `provenance`, `age_days`, `needs_contact`, `due_hint` |
| POST | `/api/commitments/scan` | `{text, source, source_id}` — **source is required** (422 without it) |
| POST | `/api/commitments/{id}/close` \| `/dismiss` | takes it off the open list |

Seek proposals with `source: "commitment"` carry `next_step.provenance` — **always show it**.
`needs_contact: true` means the commitment mentions contacting someone: the engine drafts, the
user sends. There is no path from a commitment to `send_message`, by design.

---

## OpenVault — P17a is ready to build NOW (parallel, unblocks G2.6)

Full brief: `docs/dms/packets/CLAUDE_TO_OPENVAULT_P17A_2026-07-27.md`

The load-bearing idea: **the verification key must never travel with the update.** Trust root is
pinned into the vault at install and read offline; rotation is signed by the outgoing key;
`verify_bundle` refuses a `update_generation` lower than the highest already accepted (replaying a
genuinely-signed *old* vulnerable bundle is the real-world failure mode, not forged signatures).
Update bundles reuse the shipped `netie_app.json` + ship gate, so **an update can never have more
power than an app the user imported by hand**. OAuth is device-code/loopback only — no client
secret on a laptop — with short-lived scoped tokens held in the vault.

**Hand back after step 2** (`trust_root` + `verify_bundle`) and Cortex starts G2.6 against a stub.

---

## Claude — paste this next (**owner picks ONE**)

### Option A — G2.6 signed update port (once OpenVault hands back step 2)  ← recommended

```text
Read docs/dms/packets/CLAUDE_TO_OPENVAULT_P17A_2026-07-27.md and
docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md §0.1 + §4.2.

Build G2.6 only (engine side):
1) update_port.py — check / fetch / verify-via-vault / stage; never auto-apply
2) Verified bundles enter the EXISTING ship gate (secrets scan → draft → human approve)
3) Offline is the default: unreachable update server = a log line, never a degraded engine
4) Tests against a vault stub: forged signature refused; replayed lower update_generation
   refused; tampered content refused; no network in the verify path; nothing applies without
   the human approve step

Do not touch dag_runner.py, hooks.py, demo/dms-ui/**.
Keep silence litmus green. DB_PATH monkeypatch only — including any store a new hot path touches.
Update STATUS.md + NEXT_LANES.md when done.
```

### Option B — G2.5b: close the commitment loop from run outcomes

```text
Commitments today are closed by hand. Teach the engine to notice when a commitment was
actually fulfilled (a matching routine ran and met its predicates) and propose closing it —
still confirm-gated, still provenanced, and the close itself stays a user action.
Feed accepted closes into action_value as explicit outcomes.
```

<details><summary>Superseded — G2.5 prompt (shipped 2026-07-27)</summary>

### Option A — G2.5 pattern-armed assist  ← recommended

```text
Read docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md §3.2 and docs/dms/packets/NEXT_LANES.md.

Build G2.5 only:
1) Forget-recovery: surface commitments made in past chat / /fire payloads that were never closed
2) Arm them as seek proposals with provenance ("you said this on <date>", source id kept)
3) Confirm-gated end to end — no_unconsented_contact stays absolute; never auto-contact anyone
4) Tests: a commitment buried in old text becomes a proposal; provenance shown; nothing sends;
   untrusted payload text stays wrapped through the whole path

Do not touch dag_runner.py, hooks.py, demo/dms-ui/**.
Keep silence litmus green. DB_PATH monkeypatch only — including action_event/action_value in any
fixture that runs work (see the leak-class rule below).
Update STATUS.md + NEXT_LANES.md when done.
```

### Option B — G2.6 signed update port + minimal OAuth (needs OpenVault P17a)

```text
Coordinate with the OpenVault lane first — this slice depends on P17a landing.
Read docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md §6.
```

*(superseded — G2.5 shipped)*

</details>

<details><summary>Superseded — G2.4 prompt (shipped 2026-07-27)</summary>

```text
Read docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md §4 and docs/dms/packets/NEXT_LANES.md.

Build G2.4 only:
1) action_event.py — one compact trace per run (goal_id, osr band, action_kind, predicates,
   collapse, cost, latency, outcome, initiative proactive|reactive)
2) Retention + compression so traces stay trainable without unbounded growth
3) Feed run outcomes into action_value automatically, tagged as inferred — user Accept/Dismiss
   must stay the stronger signal (product law 6), so weight inferred outcomes lower
4) Tests: schema stable; fields ranking depends on survive compression; no prose/PII
   (same policy as goal_audit); inferred outcomes never outrank an explicit user decision

Do not touch dag_runner.py, hooks.py, demo/dms-ui/**.
Keep silence litmus green. DB_PATH monkeypatch only — and check any test whose module gained a
new store (the G2.2→G2.3 leak came from exactly that).
Update STATUS.md + NEXT_LANES.md when done.
```

*(Both G2.4 options above are now shipped or superseded.)*

</details>

---

## Cursor — paste this next

```text
1) G2.3 has landed — surface the OSR band on /fire results: show band + the assumptions[] sentence
   ("I haven't seen anything like this before, so I'll plan it out step by step").
   Bands: known | near | open. Do NOT show novelty_score as a bare number.
2) Optional live CDP (machine may be slow — 60s+ health OK): Seek → Accept → re-Seek →
   confirm accepted title moved up; assert value_why visible; assert Not audited only when audit.ok=false.
3) Deep-link: seek proposal action=propose for a draft app → open Apps hub focused on that app.
4) Refresh NEXT_LANES.md with Claude's following slice.
```

---

## Backlog

| ID | Lane | Status |
|----|------|--------|
| G2.3 OSR | Claude | **SHIPPED** 2026-07-27 — contract above |
| G2.4 telemetry | Claude | **SHIPPED** 2026-07-27 — contract above |
| G2.5 pattern-armed | Claude | **SHIPPED** 2026-07-27 — contract above |
| G2.6 update/OAuth | Claude + OpenVault | **P17a brief delivered — OpenVault can build now**; Claude starts after their step 2 |
| Seek CDP / app deep-link | Cursor | optional this turn |

---

## Test + environment rules (learned the hard way, 2026-07-27)

1. **Golden DMS benchmarks are intermittently flaky (~15%) — open, unclaimed.**
   `test_accuracy_benchmark::core_tier`, `test_q2_answer_engine::…zero_confident_wrong` and
   `…scalar_question…` each failed on a different run, then 11 consecutive runs went green and it
   could not be reproduced on demand. Ruled out: OSR, test ordering, and the live engine alone.
   All three assert exact query results against the shared `packs/data/dms_ops.db`. **Suggested
   Cursor lane task:** give those benchmarks a dedicated DB copy (or mark them exclusive) so no
   other process can move the data underneath them. Until then, a single failure in one of these
   three is not evidence that a change broke anything — re-run before investigating.
   > **Superseded 2026-07-27** (see `STATUS.md`, DMS router audit): the cause was the DuckDB
   > exclusive file lock on `data/dms_demo.duckdb`, not `packs/data/dms_ops.db`. Fixed by
   > `get_connection(..., read_only=True)`. The dedicated-DB-copy task is not needed.
2. **When a module gains a new store, audit every older test that touches it.** Hit twice now:
   G2.2 gave the seeker `action_value` + the F1 ledger (two old fixtures then wrote goal-ledger
   rows into the tracked DMS ops DB); G2.4 gave *every run* an `action_event` trace, so four more
   fixtures needed isolating. **Rule: after wiring a new store into a hot path, grep for every test
   that exercises that path and patch its DB_PATH in the same commit.**
3. `LEDGER_DB_PATH = None` means **the real pack ledger**. Always monkeypatch it in tests.
4. **The ops DB is generated, not tracked (2026-07-29).** `packs/data/dms_ops.db` was in git, so
   every local run re-dirtied the tree — a bare `pytest tests/dms/test_q2_answer_engine.py` bumps
   `support_count` on 45 learned query skills, because those tests do not monkeypatch `DMS_OPS_DB`
   before the answer engine runs. Now gitignored and rebuilt by `python -m scripts.seed_ops_db`
   (schema via each owner's `init_*_schema`, ontology from pack YAML, deterministic demo bins;
   ledger and skill tables start empty). Verified: the full suite is unchanged with the file
   absent, so no seed step is needed before pytest in CI. Rule 2 still applies — isolate the DB
   in new tests; a gitignored file is quieter, not isolated.

---

## Standing product law

1. Engine does the work; user states intent.  
2. Guesses always visible.  
3. Approval + secrets stay manual.  
4. **Active > reactive** — silence litmus stays green.  
5. Confidence never authorises irreversible action.  
6. Ranking learns from **user** decisions first. As of G2.4 this is enforced arithmetically, not
   by convention: explicit weight 1.0 vs inferred 0.25, and inferred evidence is capped at the
   explicit weight once any user decision exists — 200 successful auto-runs cannot overturn one
   Dismiss. Any future signal source must enter as `inferred` unless a human pressed something.
