# DMS BRAIN → CORTEX GOVERNED-LEARNING UPGRADE
**Build plan for Cursor planning mode. Seven features. Ship one, prove it, then the next.**

`pack: packs/dms/` • backend FastAPI • frontend Next.js 14 • DuckDB + Supabase(Postgres) • local tiers T0/T1 (vLLM) • compliance engine = deterministic YAML→Python

---

## 0. What you have vs. what Cortex makes possible

### Current DMS Brain (today)
- 6 linked tables, ~25k rows (warehouse/logistics)
- DuckDB (analytics) + Supabase (Postgres + auth)
- Next.js 14 demo UI, role-based display
- NL → SQL query routing
- **sqlglot** SQL guardrails (parse + allowlist, injection-resistant)
- Data entry + steward-gated approval flow
- Sits on Cortex v2 substrate: DAG runner, cost ledger, multi-tier routing, **deterministic compliance engine**, hybrid RAG — all present, 58 tests green

### The upgrade (what this plan ships)
| Capability you asked for | What it actually is | Feature |
|---|---|---|
| "chat space when customer sends message, stop manual drop" | Governed inbound message intake + chat surface, data stays on-box | **F2** |
| "auto predict tasks with local LLM" | Intent classify → suggest most-done task for that intent | **F3 + F4** |
| "predict most done task when received similar message" | Vector + frequency × success-rate ranking over history | **F4** |
| "constantly think and learn, record, store behaviour inside" | Log every (message→task→outcome), batch-improve, human-gated promotion | **F4 + F6** |
| "retain compliance + governance engine" | Compliance gate on every suggested action before it's actionable | **F5** |
| "data safely saved between us, safe, governed" | Hash-chained audit ledger + encryption + RLS + PII redaction | **F1 + F7** |
| "harder than SHA / bitcoin encryption" | Tamper-evident hash-chained ledger (the part that's genuinely correct) | **F1** |

### The end-to-end shape after all seven
```
customer msg ─► [F2 intake/chat] ─► [F3 classify intent+sentiment]
        ─► [F4 suggest top task(s) from learned history]
        ─► human approves/edits ─► [F5 compliance gate] ─► action executes
        ─► [F1 every step written to tamper-evident ledger]
        ─► [F6 approved chain recorded as reusable skill] ─► sharpens next F4 prediction
   (F7: all of it encrypted at rest, RLS-scoped, PII never enters a prompt)
```

---

## 1. The security reframe (read once, then it's baked into the features)

**Don't chase exotic crypto.** AES-256 is unbroken. The threats that actually hit data platforms are access-control failures, leaked credentials, injection, and misconfiguration. So the security you ship is the boring, correct stack — packaged as four sellable governance features:

| Sellable feature | Real mechanism | Where |
|---|---|---|
| **Tamper-evident audit ledger** | SHA-256 hash chain + optional Ed25519 signature, append-only | F1 |
| **Sovereign encryption** | AES-256-GCM at rest (envelope), TLS 1.3 in transit, keys never leave the box | F7 |
| **Row-level access governance** | RBAC (app) + Postgres RLS (Supabase) | F7 |
| **Injection-proof query + action layer** | sqlglot guardrails (have) + parameterization + F5 compliance gate | F5/F7 |
| **PII never leaves the building** | detect + redact at ingest and *before any T2/T3 prompt* | F7 |

**Where your "bitcoin" instinct is correct:** a hash-chained append-only ledger *is* blockchain-grade integrity. That's F1, and it's a genuine differentiator for a governance platform. You don't need a blockchain, you need the Merkle/hash-chain property, on Postgres.

**Forward note (not now):** post-quantum crypto (NIST standardized ML-KEM / ML-DSA in 2024) matters only when you have bank/enterprise clients demanding crypto-agility. Don't build it pre-revenue. Note it in the roadmap and move on.

---

## 2. How to run this in Cursor

1. Drop `cortex-dms.cursorrules` at repo root (or split into `.cursor/rules/*.mdc`). It loads on every prompt.
2. For **each feature, in order**: open **planning mode**, paste that feature's prompt, let it produce a plan, **review the plan**, then execute. One subagent per feature.
3. **Do not parallelize** — F4 depends on F1+F3, F5 depends on F4, F6 depends on F5. Sequential only.
4. After each feature: run its smoke test **and** the full suite (`pytest`). Green before you move on.
5. Keep `CHANGELOG_DMS.md` updated (the rules file tells the agent to append to it), so each subagent inherits context from the last.

> Every prompt below is self-contained. The **ANTI-SCOPE** block is load-bearing — it's what stops a subagent from rewriting your DAG engine or touching the 58 passing tests.

---

## FEATURE 1 — Tamper-Evident Audit Ledger (the spine)

```
CONTEXT
You are working in the Cortex v2 repo, pack `packs/dms/`. There is an existing
deterministic compliance engine and a cost ledger. Do NOT modify those.
This feature adds an append-only, hash-chained audit ledger that every later
feature writes to. It is the integrity backbone and a customer-facing governance feature.

GOAL
A tamper-evident ledger: each entry is hash-chained to the previous one, so any
edit to history is detectable. Append + verify only. No update, no delete.

BUILD EXACTLY THIS
1. Postgres table `dms_audit_ledger` (Supabase migration):
   - id            uuid pk default gen_random_uuid()
   - seq           bigint, monotonic, unique, not null         -- 0,1,2,...
   - actor         text not null                                -- user id or 'system'
   - event_type    text not null                                -- e.g. 'message.ingested'
   - payload       jsonb not null                               -- the event body
   - prev_hash     char(64) not null                            -- hex sha256, genesis = 64 zeros
   - entry_hash    char(64) not null unique                     -- hex sha256, see formula
   - created_at    timestamptz not null default now()
   - signature     text null                                    -- optional Ed25519 over entry_hash
   Constraint: REVOKE update/delete from app role (append-only). Add a DB trigger that
   raises on UPDATE or DELETE.

2. `packs/dms/audit/ledger.py`:
   - canonical_json(obj) -> str   # sorted keys, no whitespace, UTF-8 — deterministic
   - compute_entry_hash(seq, prev_hash, payload, created_at_iso) -> str:
        return sha256(f"{seq}|{prev_hash}|{canonical_json(payload)}|{created_at_iso}").hexdigest()
   - append(actor, event_type, payload) -> LedgerEntry:
        # SELECT seq, entry_hash of last row FOR UPDATE (serialize); genesis prev = '0'*64
        # compute hash, insert, return entry
   - verify(start_seq=0) -> VerifyResult{ ok: bool, broken_at: int|None }:
        # walk chain; recompute each entry_hash; check each row.prev_hash == previous.entry_hash
   - (optional) sign with Ed25519 key from secrets if CORTEX_LEDGER_SIGNING_KEY set.

3. FastAPI router `packs/dms/api/audit.py`:
   - GET  /dms/audit?from_seq=&limit=   -> paginated entries (RLS-scoped later in F7)
   - POST /dms/audit/verify             -> runs verify(), returns VerifyResult
   Pydantic models hoisted to module level. Import Request from starlette.requests.

IMPLEMENTATION NOTES
- Serialize appends (row lock on the tail or an advisory lock) so seq + prev_hash stay correct
  under concurrency. Last-write-wins is NOT acceptable here.
- created_at must be the exact value hashed — store the same ISO string you hash.
- No __future__ annotations import in FastAPI modules.

ANTI-SCOPE — DO NOT:
- Touch the DAG engine, cost ledger, compliance engine, or hybrid RAG.
- Add any blockchain, P2P, consensus, or external chain. This is a local hash chain only.
- Modify or break any of the 58 existing tests.
- Build UI. Ledger viewer comes later.

ACCEPTANCE CRITERIA
- append() then verify() returns ok=True for a chain of 100 entries.
- Manually UPDATE one payload in the DB → verify() returns ok=False with correct broken_at.
- DB trigger blocks UPDATE and DELETE from the app role.
- Concurrent appends (20 parallel) produce a gap-free, valid chain.

SMOKE TEST (add to tests/dms/test_audit_ledger.py)
- test_chain_append_and_verify_ok
- test_tamper_detected
- test_append_only_enforced
- test_concurrent_appends_consistent
Run: pytest tests/dms/test_audit_ledger.py -q  AND  pytest -q (full suite stays green)

ON DONE: append a line to CHANGELOG_DMS.md describing F1.
```

---

## FEATURE 2 — Inbound Message Intake + Governed Chat Space

```
CONTEXT
Cortex v2, pack packs/dms/. F1 (audit ledger) exists. Today customer messages are
dropped in manually. This feature gives them a home: an intake endpoint + a chat
surface in the existing Next.js DMS UI. Data stays on-box.

GOAL
A customer message can arrive (via API now; WhatsApp BSP later via WHATSAPP_BSP
placeholder), lands in a thread, is persisted, and is visible in a chat pane. Every
arrival is written to the F1 ledger.

BUILD EXACTLY THIS
1. Tables (Supabase migration):
   - dms_threads(id uuid pk, external_ref text, customer_label text, status text
       default 'open', created_at, updated_at)
   - dms_messages(id uuid pk, thread_id uuid fk, direction text check in('inbound','outbound'),
       sender text, body text, lang text null, created_at)
2. Backend packs/dms/api/inbox.py:
   - POST /dms/inbox            body{ external_ref, sender, body } -> creates/finds thread,
       inserts inbound message, ledger.append('message.ingested', {...}), returns thread+message.
   - GET  /dms/threads?status= -> list
   - GET  /dms/threads/{id}    -> thread + messages ordered by created_at
   - POST /dms/threads/{id}/reply  body{ body } -> inserts outbound message (human-sent for now),
       ledger.append('message.sent', {...})
3. Frontend: a Chat pane in the DMS UI (reuse existing design tokens, no chat bubbles per house
   style, mono for data). Left = thread list, right = selected thread messages + a reply box.
   Use the existing role-based access wrapper. NOTE: gradient-critical elements need inline styles
   (Tailwind purges backgroundImage).

IMPLEMENTATION NOTES
- No PII redaction yet (F7) — so for now intake only synthetic/anonymized data. Add a TODO marker
  `# PII-GATE-F7` everywhere a real customer field is stored.
- Pydantic models at module level; Request from starlette.requests.

ANTI-SCOPE — DO NOT:
- Build classification, suggestions, or auto-replies (F3/F4).
- Connect a real WhatsApp/BSP integration — stub the interface behind WHATSAPP_BSP.
- Touch F1 internals beyond calling ledger.append().
- Break existing tests.

ACCEPTANCE CRITERIA
- POST /dms/inbox creates a thread + inbound message and one ledger entry.
- GET thread returns messages in order.
- Reply inserts an outbound message + ledger entry.
- Chat pane renders threads and messages; reply box posts.

SMOKE TEST (tests/dms/test_inbox.py)
- test_inbound_creates_thread_and_ledger_entry
- test_thread_fetch_ordered
- test_reply_appends_outbound_and_ledger
Run feature test + full suite green.

ON DONE: update CHANGELOG_DMS.md.
```

---

## FEATURE 3 — Intent + Sentiment Classification (local, T0/T1)

```
CONTEXT
Cortex v2, packs/dms/. F2 chat exists. Add local classification on every inbound message.
Runs on T0/T1 only — NEVER call a BIG_API (T3) in this hot path.

GOAL
Each inbound message gets: intent label, sentiment score, language mix. Stored on the
message and written to the ledger. Fast (<150ms target on CPU/L4).

BUILD EXACTLY THIS
1. packs/dms/nlp/classify.py:
   - detect_language(text) -> {ms,en,zh,...} weights  (T0 fasttext)
   - classify_intent(text) -> { intent: str, confidence: float }
        DMS intent set (warehouse/logistics): one of
        {check_stock, order_status, request_quote, schedule_pickup, report_issue,
         update_address, complaint, chit_chat, other}
        v0 = rules + embedding-NN over a seed set; leave a hook for a fine-tuned classifier later
        (JUDGMENT_CLASSIFIER_VERSION placeholder).
   - sentiment(text) -> float in [-1,1]
   - classify(text) -> Classification{ intent, confidence, sentiment, lang_mix }
2. Wire into F2 intake: after insert, run classify(), store on dms_messages
   (add columns intent, intent_conf, sentiment, lang jsonb via migration),
   ledger.append('message.classified', {...}).
3. Surface in chat pane: a small mono tag per inbound message (intent • sentiment color).

IMPLEMENTATION NOTES
- Embedder is EMBEDDER_MODEL placeholder (BGE-M3 today) — reuse the existing embedder service,
  do not spin a new one.
- All local. If you find yourself adding an Anthropic/OpenAI call here, STOP — wrong tier.

ANTI-SCOPE — DO NOT:
- Build task suggestion (F4) — only classify.
- Fine-tune a model now. v0 is rules + NN. Leave the hook.
- Touch routing/cost-ledger internals beyond reading the embedder.
- Break existing tests.

ACCEPTANCE CRITERIA
- classify() returns all four fields for EN, BM, and mixed BM/EN/中文 inputs.
- An inbound message persists intent/sentiment/lang and emits a ledger entry.
- p95 latency under target on a representative batch (assert it's local-only).

SMOKE TEST (tests/dms/test_classify.py)
- test_classify_returns_all_fields
- test_mixed_language_handled
- test_intent_in_allowed_set
- test_no_bigapi_called   # monkeypatch the router; assert T3 never invoked
Feature test + full suite green.

ON DONE: update CHANGELOG_DMS.md.
```

---

## FEATURE 4 — Task Library + Suggestion Engine (predict + learn)

```
CONTEXT
Cortex v2, packs/dms/. F1 ledger, F2 chat, F3 classification exist. This is the core
"predict the most-done task for this kind of message, and get better over time" engine.

GOAL
When an inbound message is classified, surface the top-N task suggestions for it, ranked
from history. When a human picks/edits one, record it as a labeled outcome that improves
future ranking. Learning is RECORD-now / BATCH-improve / human-gated promote — NOT live
model training.

BUILD EXACTLY THIS
1. Tables (migration):
   - dms_tasks(id uuid pk, name text, description text, template jsonb, active bool default true)
       -- the catalog of executable tasks (e.g. 'create_pick_order', 'send_quote', 'open_ticket')
   - dms_task_events(id uuid pk, message_id uuid fk, thread_id uuid fk, intent text,
       msg_embedding vector, suggested_task_ids jsonb, chosen_task_id uuid null,
       outcome text null check in('success','failed','abandoned',null),
       created_at)
2. packs/dms/tasks/suggest.py:
   - suggest(message) -> list[TaskSuggestion]:
        a) embed message (existing embedder)
        b) candidates = tasks historically chosen for: this intent  ∪  k-NN of similar past messages
        c) score(task) = w1*frequency_for_intent + w2*success_rate + w3*recency
           (start w1=0.4, w2=0.4, w3=0.2; expose in config)
        d) return top-N (default 3) with scores + a human-readable reason
        e) write 'task.suggested' to ledger; insert dms_task_events row (chosen null)
   - record_choice(event_id, chosen_task_id) -> updates row, ledger 'task.chosen'
   - record_outcome(event_id, outcome) -> updates row, ledger 'task.outcome'
3. Nightly batch packs/dms/tasks/learn.py (cron):
   - recompute per-intent frequency + success-rate stats into a `dms_task_stats` table
   - this is the "learning" — deterministic stats refresh, reproducible, auditable
   - DO NOT mutate model weights; this is ranking-stat refresh only
4. Frontend: in the chat pane, below a classified inbound message, show the top-3 suggested
   tasks as selectable chips with the reason + score. Selecting one calls record_choice.
   (Execution + compliance gate is F5 — for now selecting just records the choice.)

IMPLEMENTATION NOTES
- Cold start: when history is thin, fall back to intent→default-task mapping (seed table).
- Vector search via the existing Qdrant/pgvector setup — don't add a new store.
- Keep suggest() under the F3 latency budget; it's T0/T1 + a vector query, no T3.

ANTI-SCOPE — DO NOT:
- Execute tasks or call the compliance engine (that's F5).
- Fine-tune or online-train any model. Learning = batch stat refresh only.
- Auto-send anything to a customer.
- Break existing tests.

ACCEPTANCE CRITERIA
- suggest() returns ranked tasks with reasons for a classified message.
- Cold-start path returns the seeded default when no history.
- record_choice + record_outcome persist and emit ledger entries.
- After seeding outcomes and running learn.py, a task with higher success-rate ranks above a
  more-frequent-but-failing one (assert the ranking shift).

SMOKE TEST (tests/dms/test_suggest.py)
- test_suggest_ranked_with_reasons
- test_cold_start_default
- test_choice_and_outcome_recorded
- test_batch_learning_shifts_ranking
- test_suggestion_path_is_local_only
Feature test + full suite green.

ON DONE: update CHANGELOG_DMS.md.
```

---

## FEATURE 5 — Compliance Gate on Suggested Actions

```
CONTEXT
Cortex v2, packs/dms/. The deterministic compliance engine ALREADY EXISTS in Cortex
(YAML rulesets compiled to Python checks). F4 suggests + records task choices. This feature
makes a chosen task pass compliance BEFORE it can execute, and records the verdict.

GOAL
No suggested task becomes an executable action until the compliance engine returns pass
(or pass-with-warnings that a human acknowledges). Every verdict is logged.

BUILD EXACTLY THIS
1. packs/dms/compliance/dms_rules_v1.yaml — a small starter ruleset for DMS task execution, e.g.:
   - quote_total_must_be_present (for send_quote)
   - pickup_address_required + valid format (for schedule_pickup)
   - no_outbound_to_unverified_customer (gate sends behind identity flag)
   - value_threshold_requires_human (task value > X MYR must be human-approved, never auto)
   Reuse the EXISTING engine to load + run this ruleset. Do NOT write a new engine.
2. packs/dms/tasks/gate.py:
   - check_task(event_id, task_id, filled_template) -> ComplianceVerdict{ status, violations[] }
        status in {pass, warn, fail}
   - on pass/warn: mark dms_task_events.gate_status; ledger 'task.gate_passed' (or warn)
   - on fail: block; ledger 'task.gate_failed' with violations
3. Wire into the chat flow: selecting a task (F4) now routes through check_task. UI shows the
   verdict — green pass, amber warn (with an "acknowledge & proceed" affordance for the steward),
   red fail (blocked, shows which rule).

IMPLEMENTATION NOTES
- LLMs may EXTRACT fields from unstructured text (T2 max), but rules fire on STRUCTURED data only.
  Never let an LLM decide pass/fail. The verdict is deterministic.
- The value_threshold rule is the safety rail: high-value or irreversible actions are never
  auto-executed — always human-gated.

ANTI-SCOPE — DO NOT:
- Rewrite or fork the existing compliance engine — call it.
- Actually send to customers / mutate warehouse data yet (execution wiring is a later, separate
  task once a real client is in the loop). For now "execute" = produce the approved action object.
- Break existing tests.

ACCEPTANCE CRITERIA
- A task missing a required field returns fail with the specific violation; it cannot proceed.
- A pass verdict marks the event executable and logs it.
- A value over threshold returns warn/human-required, never auto-pass.
- Verdicts are deterministic (same input → same verdict, 100/100 runs).

SMOKE TEST (tests/dms/test_gate.py)
- test_missing_field_blocks
- test_pass_marks_executable
- test_value_threshold_requires_human
- test_verdict_deterministic
- test_llm_never_decides_verdict
Feature test + full suite green.

ON DONE: update CHANGELOG_DMS.md.
```

---

## FEATURE 6 — Behaviour Capture → Reusable Skill (the learning loop closes)

```
CONTEXT
Cortex v2, packs/dms/. The runtime already has a `skills/` concept. F1–F5 produce a full
chain: message → classified → task chosen → compliance-passed → action. This feature records
a successful approved chain as a reusable internal skill, so the next similar message is handled
faster. Consented, internal-only, anonymized — never covert.

GOAL
When a chain completes with outcome=success, capture it as a structured skill card stored
INSIDE the company's own store. These skills feed F4's suggestions and document how work is done.

BUILD EXACTLY THIS
1. Table dms_skills(id uuid pk, intent text, trigger_pattern text, embedding vector,
     task_id uuid, template jsonb, support_count int default 1, success_count int default 1,
     last_used_at, created_by text, consented bool default true)
2. packs/dms/skills/capture.py:
   - capture_from_event(event_id):
        # only if gate=pass AND outcome=success
        # upsert a skill keyed by (intent, normalized trigger) — increment support/success if exists
        # store the message embedding as the trigger vector
        ledger.append('skill.captured', {...})
   - feed: F4.suggest() now also queries dms_skills (a captured skill that matches strongly
        boosts its task in the ranking). Wire this read into suggest() scoring (add w4*skill_match).
3. A simple "Skills" admin view: list captured skills with support/success counts, the intent,
   and a toggle to deactivate one. Stewards can prune.

CONSENT / GOVERNANCE (required, not optional)
- Add a config flag dms_skill_capture_enabled (default OFF). Capture only runs when a workspace
  has explicitly enabled it. Document this in the admin view ("recording is on / off").
- Store created_by and consented=true; no covert capture path exists.
- This is internal-only: skills never leave the box, never go to a BIG_API.

ANTI-SCOPE — DO NOT:
- Capture from failed/abandoned chains or when the gate didn't pass.
- Add any export of skills off-box.
- Train/fine-tune a model on captured data. Skills are structured cards + stats, retrieved at
  suggest time. (Offline fine-tuning, if ever, is a separate human-gated project.)
- Build a default-ON capture. It must be opt-in.
- Break existing tests.

ACCEPTANCE CRITERIA
- A successful gated chain with capture enabled creates/updates one skill + a ledger entry.
- Capture does nothing when the flag is OFF or the chain failed.
- A captured skill measurably boosts its task in a subsequent suggest() for a matching message.
- Deactivating a skill removes it from suggestion influence.

SMOKE TEST (tests/dms/test_skill_capture.py)
- test_capture_only_on_success_and_consent
- test_capture_disabled_is_noop
- test_captured_skill_boosts_suggestion
- test_deactivate_skill_excluded
Feature test + full suite green.

ON DONE: update CHANGELOG_DMS.md.
```

---

## FEATURE 7 — Security Hardening Pass (ship before any real customer data)

```
CONTEXT
Cortex v2, packs/dms/. F1–F6 built the loop on synthetic data (PII-GATE-F7 markers everywhere).
This feature makes it safe for real data: encryption, access control, PII redaction, secrets,
rate limiting. After this, the PII-GATE-F7 markers can be removed.

GOAL
Real customer data can be handled with: encryption at rest + in transit, row-level access,
PII detected and redacted (and NEVER placed in any LLM prompt), secrets out of code, and
rate-limited endpoints.

BUILD EXACTLY THIS
1. PII detect + redact — packs/dms/security/pii.py:
   - detect(text) -> spans  (NRIC, phone, email, credit-card via regex + optional local NER)
   - redact_for_prompt(text) -> text with PII replaced by typed placeholders ([NRIC], [PHONE]...)
   - HOOK: every code path that builds an LLM prompt (F3 extraction, any T2 call) MUST call
     redact_for_prompt first. Add a single choke-point wrapper so PII cannot reach a model.
   - At ingest (F2): store the redacted body for display/search; store the raw body
     AES-256-GCM-encrypted in a separate column/table, access-controlled.
2. Encryption at rest — packs/dms/security/crypto.py:
   - envelope encryption: a local master key (from secrets, see #4) wraps per-record data keys.
   - encrypt_field / decrypt_field for sensitive columns. AES-256-GCM (authenticated).
   - For the NAS: also enable full-disk encryption + Postgres at-rest where available.
3. Access control:
   - Enforce RBAC at the API (roles: viewer, steward, admin) AND enable Postgres RLS in Supabase
     so a query cannot return rows outside the caller's scope even if the app layer is bypassed.
   - Apply RLS to dms_messages, dms_threads, dms_task_events, dms_skills, dms_audit_ledger reads.
4. Secrets:
   - Remove any secret from code/env-in-repo. Use SOPS+age (pragmatic) now; document a Vault
     path for the enterprise story later. Keys: ledger signing key, master encryption key,
     BIG_API_PLACEHOLDER creds, WHATSAPP_BSP creds.
5. Transport + edge:
   - TLS 1.3 termination at the reverse proxy (Caddy/nginx), modern ciphers, HSTS.
   - Rate limiting: token-bucket per user + per IP on /dms/inbox and auth endpoints.
6. Reinforce injection defense: confirm sqlglot guardrail rejects DDL/DML on read paths +
   parameterized queries everywhere; structured-output + tool-allowlist on any LLM tool call
   (Cortex already enforces this — verify, don't rebuild).

IMPLEMENTATION NOTES
- The PII choke-point is the single most important control here. There must be NO code path that
  sends un-redacted text to any model. Add a test that fails if raw PII reaches the prompt builder.
- Do not invent crypto. Use a vetted library (cryptography / libsodium). AES-256-GCM, Ed25519,
  Argon2id for any password hashing.

ANTI-SCOPE — DO NOT:
- Implement post-quantum crypto now (note it in roadmap).
- Build a custom cipher or custom key-exchange. Use standard primitives.
- Roll your own auth — extend Supabase auth + RLS.
- Break existing tests.

ACCEPTANCE CRITERIA
- A message containing an NRIC/phone is stored redacted; the raw is encrypted; the model prompt
  built from it contains placeholders, not the PII (assert).
- Encrypt then decrypt round-trips; tampered ciphertext fails the GCM auth tag.
- A viewer-role caller cannot read steward-only rows even via a direct query (RLS proven).
- No plaintext secret remains in the repo (scan).
- Rate limit returns 429 past the bucket.

SMOKE TEST (tests/dms/test_security.py)
- test_pii_redacted_before_prompt   # the critical one
- test_raw_pii_encrypted_at_rest
- test_aes_gcm_tamper_fails
- test_rls_blocks_out_of_scope_read
- test_rate_limit_429
- test_no_secret_in_repo
Feature test + full suite green. Then remove PII-GATE-F7 markers.

ON DONE: update CHANGELOG_DMS.md. Tag this as the "real-data-ready" milestone.
```

---

## 3. Sequencing summary (do not reorder)

```
F1 ledger ─► F2 chat ─► F3 classify ─► F4 suggest+learn ─► F5 compliance gate
                                            └─► F6 skill capture (closes the loop)
F7 security hardening  ◄── ship before ANY real customer data touches F2
```

Ship F1–F2 and you have a governed chat space. Add F3–F4 and you have task prediction. Add F5–F6 and you have the learning, compliant second brain. F7 makes it safe for production. Each is a demoable increment — show a client after F4, not after F7.

## 4. What NOT to build yet (scope rail)
No WhatsApp BSP integration (stub it), no real warehouse-mutation execution until a paying client is in the loop, no model fine-tuning, no post-quantum crypto, no blockchain. Those are all "after a client pays" items.
