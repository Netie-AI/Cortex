# Retrieval & Agentic Orchestration — Findings (as of 2026-07-20)

Research for **Netie Cortex**: an ADAPTIVE orchestration layer over OpenDMS (local-first; Python 3.10/3.14,
FastAPI, DuckDB + DuckLake lakehouse, optional Postgres; Windows 11 + RTX 4070 12GB). Small closed corpus
(supplier contracts, warehouse docs, 6-table warehouse schema). Companion doc: `NL2SQL_ACCURACY_2026.md`
(the 4-layer answer engine: certified → governed metrics → verified free-form → abstain).
Owner's question: *"how do they achieve near-perfect retrieval — RAG? agentic tool orchestration? DAG vs cyclic
graph vs LLM-driven planner? — and how do we route adaptively by task/file/data/operation type, with fallback?"*

Tags: **[V]** verified against primary source this session (fetched or seen in official docs/leaderboard);
**[R]** reported by a secondary source or surfaced via search snippet (title/abstract only, not independently read);
**[I]** inference / recommendation. Foundational arXiv papers (2022-2025) are cited by their well-known IDs; 2026-dated
papers were surfaced by search this session and are tagged [R] unless otherwise noted.

---

## 1. High-accuracy RAG patterns (mid-2026)

**What actually moves retrieval quality (ranked by impact for a small closed corpus):**

- **Hybrid search (dense + BM25) with RRF fusion — table stakes. [V/R]** Dense embeddings catch paraphrase/semantics;
  BM25 catches exact IDs, SKUs, contract clause numbers, part numbers — exactly the tokens that dominate warehouse/contract
  text. Fuse candidate lists with **Reciprocal Rank Fusion** (weighted RRF). "Using only one leaves accuracy on the table."
  (Anthropic Contextual Retrieval, https://www.anthropic.com/engineering/contextual-retrieval).
- **Reranking (cross-encoder) — the single biggest cheap win after hybrid. [V/R]** A cross-encoder scores (query, chunk)
  jointly over a top-20..100 candidate set, returns top 3-5 to the LLM. **Cohere Rerank 3.5** (managed API, multilingual)
  and **BAAI/bge-reranker-v2-m3** (Apache-2.0, self-hostable, runs fine on the 4070) are the two defaults; NDCG@10 is close
  between them, both far above pure vector search (https://futureagi.com/blog/best-rerankers-for-rag-2026/, particula.tech
  reranker comparison). Jina-reranker-v2 and Voyage rerank-2.5 are alternatives.
- **Anthropic Contextual Retrieval — best fit for a small static corpus. [V]** Prepend a 50-100-token LLM-generated context
  blurb to each chunk *before* embedding AND before BM25 indexing ("Contextual Embeddings" + "Contextual BM25"). Prompt-caching
  the source doc makes it cheap to generate. **Contextual Embeddings + Contextual BM25 cut top-20 retrieval failures 49%;
  adding a reranker → 67% (failure rate 5.7% → 1.9%).** This is a one-time index-build cost — ideal for contracts/docs that
  rarely change (https://www.anthropic.com/engineering/contextual-retrieval; recipe: https://docs.together.ai/docs/how-to-implement-contextual-rag-from-anthropic).
- **Late interaction (ColBERT / ColPali) — for layout-heavy PDFs. [V/R]** ColBERTv2 (arXiv 2112.01488) indexes a vector
  *per token*; MaxSim late-interaction beats single-vector dense on term-precise retrieval. **ColPali** (arXiv 2407.01449)
  encodes each **PDF page as a grid of visual patch embeddings** straight from the image — preserves tables/charts/layout
  that OCR destroys, no OCR pipeline. Cost: ~1,030 patch vectors/page, ~100-500 KB index/page — a storage/latency tax that
  only pays off on visually complex docs (scanned contracts, spec sheets) (https://arxiv.org/html/2407.01449v6; spheron.network ColPali 2026).
- **GraphRAG — powerful but expensive; use the lazy variants. [V/R]** Microsoft GraphRAG (arXiv 2404.16130) wins on
  *global/multi-hop* "summarize across the whole corpus" questions but full indexing was cost-prohibitive (~$33K for large
  corpora, 2024). **LazyGraphRAG (Microsoft, June 2025)** defers community summarization to query time → ~99% indexing-cost
  reduction; **HippoRAG 2** and **LightRAG** hit similar quality at 10-30× lower cost / 6-13× lower latency via Personalized
  PageRank over LLM-extracted graphs (https://www.articsledge.com/post/lazygraphrag-retrieval-augmented-generation; WildGraphBench
  arXiv 2602.02053 shows LightRAG 71.2% / HippoRAG2 67.3% / MS-GraphRAG-global 65.4% on one domain). **[I]** For a 6-table
  schema + a few hundred docs, GraphRAG is overkill; revisit only if cross-contract "which suppliers share clause X" queries appear.
- **Agentic RAG (retrieve → grade → re-retrieve loops). [V/R]** The 2026 default for hard queries:
  - **CRAG / Corrective RAG** (arXiv 2401.15884): a lightweight retrieval-*evaluator* grades retrieved docs; low grade →
    corrective action (query rewrite / web or alt-index search); partial → knowledge refinement (strip irrelevant sentences).
  - **Self-RAG** (arXiv 2310.11511): one model emits reflection tokens ([Retrieve]/[IsRel]/[IsSup]/[IsUse]) to decide whether
    to retrieve and to self-grade support — good pattern, but needs a tuned model.
  - **Adaptive-RAG** (arXiv 2403.14403): a difficulty classifier routes easy queries to single-pass, hard ones into the loop —
    directly the "small model first, escalate" idea applied to retrieval.
  - 2026 production consensus: five patterns cover most systems — **router, ReAct, plan-and-execute, multi-agent retrieval,
    Self-RAG**, with CRAG/Adaptive-RAG/GraphRAG layered in as workload demands (https://www.brightter.com/articles/agentic-rag-five-retrieval-patterns-that-survive-production).
- **Query transforms. [V/R]** **HyDE** (arXiv 2212.10496): embed a hypothetical answer to bridge the query↔doc vocabulary gap.
  **Query decomposition**: split multi-part questions into sub-queries, retrieve each, merge — the biggest recall win on
  compound questions. Both cheap; apply selectively (they can hurt on simple lookups).

**How retrieval quality is measured (build the harness around these): [V/R]**
- **Retrieval:** Recall@k, Precision@k, MRR, **nDCG@k** (graded, position-weighted) — need a labeled (query → relevant-chunk) set.
- **End-to-end (RAGAS, https://qaskills.sh/blog/ragas-rag-evaluation-metrics-complete-guide):** **faithfulness** (are answer
  claims grounded in retrieved context — the anti-hallucination metric), **answer relevance**, **context precision**,
  **context recall**, **answer correctness**. Faithfulness/answer-relevance are *reference-free* (LLM-judge, no gold answer).
  In 2026 RAGAS also scores agentic tool-use (did the agent call the right tools in a sensible order).
- **[I]** For Netie's closed corpus, build a small labeled gold set (queries → known-relevant chunks) so recall@k / nDCG are
  measured, not vibed; use RAGAS faithfulness as the confident-wrong tripwire. LLM-judge only for prose, never numeric facts.

---

## 2. Agentic orchestration: DAG vs cyclic graph vs LLM-driven (the core question)

### 2a. Static DAG pipelines (deterministic)
- **[V/R/I]** DAGs (our `dag_runner`, dbt, Dagster, Prefect) enforce top-to-bottom flow, each step a fixed output, no revisiting.
  "DAGs work for ETL pipelines where each step produces a fixed output and never needs to revisit a prior stage"
  (folio3, atlan LangGraph writeups). **Best when** the plan is known ahead of time and reproducibility/auditability matter:
  ingest, dedup, scheduled metric refresh, compliance report generation, the certified-answer replay path. Cheapest,
  fully testable, no LLM nondeterminism. This is the backbone; keep as much work here as possible.

### 2b. Cyclic graphs (state machine with loops) — the retrieve→grade→retry sweet spot
- **[V/R] LangGraph** (LangChain): models the app as a **directed *cyclic* graph** of nodes+edges over **persistent shared
  state**; natively supports cycles, branching, retries, **checkpointing** (serialize full state after each node to
  Postgres/Redis → resume/rollback/branch), and **human-in-the-loop interrupt/approval** points. "Cycles are not a bug to
  avoid but a feature to embrace" for self-correction/iterative reasoning (folio3, atlan, spheron 2026 writeups). This is the
  canonical home for CRAG/Self-RAG grading loops and bounded retry.
- **[R] Peers:** **LlamaIndex Workflows / AgentWorkflow** (event-driven steps; natural if the stack is LlamaIndex-RAG-first);
  **Google ADK** (code-first, versioning/testing/modularity, model-agnostic though Gemini-tuned, Python/Java/Go — enterprise
  on GCP); **Burr** (state-machine library, lightweight). All express "loop until a condition/grade passes."
- **[I] When to reach for cycles:** the moment you need *bounded* retry driven by a grader (retrieval failed relevance,
  SQL failed execution, answer failed faithfulness) with checkpointing/HITL — but the set of possible steps is still known.
  The graph is explicit and auditable; the LLM chooses *edges*, not the whole plan.

### 2c. LLM-driven / planner agents (max autonomy)
- **[V/R]** Single-agent patterns (canonical papers 2022-2023):
  - **ReAct** (arXiv 2210.03629): thought → tool → observation → repeat; the default tool-calling loop.
  - **Plan-and-Execute** / ReWOO: plan up front, cheap executor runs steps (fewer LLM calls than ReAct).
  - **Reflexion** (arXiv 2303.11366): after an episode, verbalize a "lesson" and retry — **+10-20 pts on coding pass-rates**.
  - **Tree-of-Thoughts** (arXiv 2305.10601) / **LATS** (arXiv 2310.04406): search over reasoning branches (MCTS in LATS,
    unifying ReAct+Reflexion+ToT). Highest accuracy, highest cost/latency — reserve for genuinely hard, high-value tasks.
  - 2026 production shape: a Plan-and-Execute outer loop, each step a ReAct agent with its own tools, wrapped in a Reflection
    pass that re-runs failing checks (https://dev.to/gabrielanhaia/... three-agent-patterns-2026).
- **[I]** Use a full planner only when steps genuinely can't be predicted in advance (open-ended investigation, "explain this
  anomaly across sources"). Autonomy trades determinism/testability for flexibility — the opposite of what a governed data
  platform wants for its default paths.

### 2d. Multi-agent topologies + failure modes
- **[V] Anthropic "How we built our multi-agent research system"** (https://www.anthropic.com/engineering/multi-agent-research-system):
  **orchestrator-worker** (lead agent plans + spawns parallel subagents). Opus-lead + Sonnet-workers **beat single-agent Opus
  by 90.2%** on their research eval. **BUT the cautions are the headline:**
  - "**Agents typically use ~4× more tokens than chat; multi-agent systems ~15× more tokens than chat.**" Token usage alone
    explained **80%** of performance variance. Only worth it when "the value of the task is high enough to pay."
  - "**Domains that require all agents to share the same context, or involve many dependencies between agents, are not a good
    fit for multi-agent systems today.**" Most coding/debugging fails this test; research (parallel, breadth-first, exceeds one
    context window) passes.
- **[V/R] Cognition "Don't Build Multi-Agents"** vs Anthropic: not contradictory — different workloads. Rule: **single-thread
  by default; go multi-agent only for parallel, independent, tool-rich, breadth-first work.** Failure modes to design against:
  context bloat, error compounding across hops, cost blowup, agents coordinating poorly in real time
  (https://www.langchain.com/blog/how-and-when-to-build-multi-agent-systems).
- **[I]** Netie is a single-user local data platform — mostly dependency-heavy, shared-context work. **Default to
  single-agent + deterministic tools + a cyclic grading loop. Do NOT build a multi-agent swarm** unless a clearly parallel,
  breadth-first task appears (e.g., "audit all 200 contracts for clause X" → parallel per-doc workers under one supervisor).

---

## 3. Adaptive / self-improving routing (fail → escalate cheaply)

- **[V/R] Routing vs cascade — the two escalation shapes:**
  - **Routing** = one-shot: a classifier picks exactly one model/strategy *before* running. **RouteLLM** (arXiv 2406.18665)
    learns the router from preference data (matrix-factorization / causal-LLM classifiers on Chatbot Arena).
  - **Cascade** = sequential escalation: cheapest model/strategy first; **if confidence < threshold, escalate to the next
    tier** until confident or top tier reached (tianpan.co LLM routing & cascades; mbrenndoerfer model-routing).
  - Netie wants **cascade** (an explicit fallback ladder), optionally with a cheap upfront **difficulty classifier** (Adaptive-RAG
    style) to skip tiers.
- **[V/R] Confidence signals to gate escalation** (cheapest → strongest):
  1. **Which layer answered** (certified > template > free-form) — structural, from the NL2SQL doc.
  2. **Token/logit confidence**: high-confidence generations concentrate probability mass; spread = uncertain → escalate
     (the core cascade mechanism). Calibrate (isotonic/entropy) — uncalibrated confidence lies.
  3. **Execution-grounded checks** (strongest, cheap, deterministic): SQL runs? result non-empty & within sane bounds?
     retrieved context passes a relevance grade? faithfulness check passes? These beat any self-reported confidence.
  4. **N-candidate agreement / self-consistency**: sample N, execute/compare, vote; disagreement = escalate.
  5. **Verifier / LLM-as-judge**: last resort; **pitfalls** — position & verbosity bias, and (per NL2SQL doc) adding schema
    to the judge prompt made judging *worse*. Never let a judge certify a number a deterministic check could.
- **[V/R] Test-time compute allocation:** 2026 work (BEST-Route arXiv 2506.22716; "Resample or Reroute?" arXiv 2607.08665;
  "Cluster, Route, Escalate" arXiv 2606.27457; UCCI calibrated-uncertainty cascade arXiv 2605.18796) converges on:
  **combine model selection with adaptive compute** — spend more samples/bigger model *only* on queries a cheap confidence
  signal flags as hard. Matches the NL2SQL finding that test-time compute + execution-grounded selection beats bigger single-shot models.
- **[I] How production decides "this failed, try stronger":** a **retry budget** (e.g., ≤2 escalations) + a **grader at each
  rung** (execution result / relevance / faithfulness) + a **verifier gate** before delivery. If the top rung still fails the
  gate → **abstain/clarify**, never deliver unverified. Cheap because 80-95% of queries settle on rung 1.

---

## 4. Tools / skills registries for agents

- **[V/R] MCP (Model Context Protocol) is the tool surface standard.** Anthropic-introduced (Nov 2024); **donated to the
  Agentic AI Foundation under the Linux Foundation Dec 2025** (vendor-neutral). **MCP Registry launched Sept 2025**, ~2,000
  servers within months; **2026 roadmap (Mar 2026) prioritizes enterprise readiness**; native support across Anthropic/OpenAI/
  Google/Microsoft and LangChain/CrewAI/LangGraph/LlamaIndex (https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026).
- **[V/R] Tool selection at scale — "choice paralysis" is real.** Dumping all tools into context degrades accuracy and bloats
  prompts. **Tool-RAG / RAG-MCP** (arXiv 2505.03275; ScaleMCP arXiv 2505.06416): store tool definitions in a vector index,
  retrieve only the top-k relevant tools per query. Reported: with 100+ tools, naive **13.6% → Tool-RAG 43.1%** selection
  accuracy, ~½ the tokens (redhat/webscraft/machinelearningmastery 2026). **Rule of thumb: < ~20 tools → static list is fine;
  beyond that → Tool-RAG.** Netie starts small (SQL, chart, export, ingest, dedup, report, anomaly-detect) → static list now,
  Tool-RAG later.
- **[V] Code-execution-with-MCP** (https://www.anthropic.com/engineering/code-execution-with-mcp): present MCP servers as
  a code API the agent calls, loading tool definitions **on demand** rather than all upfront — cuts token overhead when tool
  counts grow. Relevant once the registry is large.
- **[V/R] Anthropic Agent Skills — reusable capability cards.** Introduced Oct 2025; **open standard published Dec 18 2025 at
  agentskills.io**; ~25+ products (OpenAI, Microsoft, JetBrains, Cursor, Gemini CLI, Goose) shipped compatible impls by Mar
  2026. A **Skill = a folder with `SKILL.md`** (YAML frontmatter: `name`, `description`) + optional examples/scripts.
  **Progressive disclosure (3 stages):** discovery (load only name+description) → activation (read full SKILL.md when task
  matches) → execution (load referenced files / run bundled code as needed). This keeps context lean while scaling capabilities
  (https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills).
- **[V/R] Structured outputs / constrained decoding for reliable tool args.** By early 2026 OpenAI, Anthropic, Google all ship
  native structured output; **`strict: true`** compiles the JSON Schema to an FSM and masks invalid tokens → **100% schema-valid
  tool arguments** (invalid-token logits set to -∞). Pair with a validation layer (Pydantic/Zod, Guardrails AI) for semantic
  checks (https://collinwilkins.com/articles/structured-output). This is how you make tool calls reliable, not hopeful.
- **[I] What belongs in Netie's governed skill/tool registry** (each a card with: name, description, input schema, output
  schema, **required compliance gate**, cost tier, and a golden test):
  `sql_query` (→ NL2SQL 4-layer engine), `semantic_metric` (governed metric compile), `chart`, `export` (gated: writes/leaves
  the system), `ingest` (gated), `dedup`, `report`, `anomaly_detect`, `doc_retrieve` (RAG over contracts), `web_lookup`
  (if ever allowed). Side-effecting/data-leaving tools (`export`, `ingest`, external sends) route through the compliance gate
  + audit ledger before execution; read-only tools don't. Constrained decoding on every tool's args; every tool has a golden test in CI.

---

## 5. Adaptivity by FILE TYPE and DATA TYPE (dispatch table)

Map each input to a retrieval+orchestration strategy so the router dispatches by type. [I], grounded in §1-4.

- **Structured tables (DuckDB/CSV/Parquet/DuckLake):** → **NL2SQL 4-layer engine + semantic layer** (see NL2SQL doc). No RAG.
  Orchestration: deterministic template first; cyclic grading loop (execute → vote → sanity rails) for free-form. Gate:
  executed-result correctness. This is Netie's primary path.
- **Long PDFs / supplier contracts:** → **layout-aware parsing** (Docling / Unstructured for digital PDFs; **ColPali** for
  scanned/visually-complex pages, no OCR) → **contextual-retrieval chunks + hybrid + reranker**. Multi-hop "compare clauses
  across contracts" → consider LazyGraphRAG. Orchestration: CRAG grade→re-retrieve loop. Gate: RAGAS faithfulness + citation-present.
- **Images / scans / diagrams:** → **VLM** (describe/extract) or OCR when text-only suffices; index page images via ColPali if
  layout matters. Gate: extraction confidence + human confirm on low confidence.
- **Time-series / streams (stock levels, shipment events):** → **windowed detectors / SQL over time buckets**, not RAG;
  anomaly-detect tool with statistical/threshold checks. Orchestration: deterministic DAG (scheduled) + alert. Gate: bounds/z-score sanity.
- **Code:** → repo-map + symbol/AST retrieval + embeddings; ReAct tool loop (read/grep/edit). Gate: tests/compile pass. (Low
  priority for Netie unless it self-manages transforms.)
- **JSON / logs / semi-structured:** → schema-infer → treat as structured (query engine) when regular; hybrid text search for
  free-form logs. Gate: parse success + field validation.

**Router key = (operation type, file type, data type, difficulty) → strategy.** Difficulty from a cheap upfront classifier
(Adaptive-RAG style) chooses how far up the ladder to start.

---

## Verdicts for Netie Cortex (adaptive router)

### (a) Decision matrix — operation/file/data type → orchestration → accuracy gate → fallback

| Task / operation | Input type | Default orchestration | Accuracy gate | Fallback (escalate) |
|---|---|---|---|---|
| Certified/known question | any | **Deterministic replay** (DAG, no LLM) | exact match to verified query | → governed-metric template |
| Metric/aggregate ("low stock", "utilisation") | structured table | **Template + slot-fill** (deterministic SQL compile) | slots resolve; execution OK; bounds sane | → verified free-form (cyclic loop) |
| Free-form analytical SQL | structured table | **Cyclic loop**: gen N → sqlglot allowlist → execute → result-vote → sanity rails | result-set agreement + rails pass | → bigger model / +samples → **abstain/clarify** |
| Doc lookup / clause find | PDF/contract | **RAG**: hybrid + contextual chunks + reranker | RAGAS faithfulness + citation present | **CRAG** grade→rewrite→re-retrieve → abstain |
| Cross-doc / global summary | many docs | **LazyGraphRAG** or decomposition + multi-retrieve | faithfulness + coverage | → orchestrator-worker (parallel per-doc) |
| Scanned / layout-heavy doc | image PDF | **ColPali** late-interaction (no OCR) | extraction confidence | → VLM describe → human confirm |
| Anomaly / monitoring | time-series | **Deterministic detector** (scheduled DAG) | bounds/z-score sanity | → analyst agent explains |
| Ingest / export / external send | any | **DAG** through **compliance gate + audit ledger** | policy pass + schema-valid (constrained decoding) | **block + notify** (never auto-escalate side effects) |
| Open-ended investigation | mixed | **Planner (Plan-and-Execute + ReAct)**, single-agent | per-step execution/faithfulness grade | reflection retry (budget ≤2) → abstain |

### (b) Recommended default RAG stack for the closed corpus (VERIFIED components)
**Contextual Retrieval chunks (Anthropic) → hybrid dense + BM25 → RRF fusion → cross-encoder rerank (bge-reranker-v2-m3
self-hosted on the 4070, or Cohere Rerank 3.5) → top 3-5 to the LLM.** Add a **CRAG grade→re-retrieve** loop for hard/low-grade
queries only. This is the pragmatic path to near-perfect recall on a small static corpus (Anthropic's own numbers: 67% fewer
retrieval failures with contextual + rerank). Defer ColPali (only for scanned/layout docs) and GraphRAG (only for cross-doc global questions).

### (c) DAG vs cyclic vs planner — concrete triggers
- **Deterministic template / DAG** when the plan is known and reproducibility matters (certified answers, metric refresh,
  ingest/export, reports). *Default — keep the maximum here.* Cheapest, fully testable, auditable.
- **Cyclic graph (LangGraph-style) with a grader** when you need **bounded retry driven by a check** (retrieval failed
  relevance / SQL failed execution / answer failed faithfulness), plus checkpointing + human-in-the-loop. Trigger: any
  retrieve→grade→retry or generate→verify→repair path.
- **Planner agent (single-agent ReAct / Plan-and-Execute + Reflexion)** ONLY when steps are genuinely unpredictable
  (open-ended investigation). **Multi-agent ONLY when work is parallel/independent/breadth-first** (Anthropic: 15× token cost,
  bad fit for shared-context/dependency-heavy work). Netie is mostly the latter → **single-agent by default.**

### (d) Fallback-ladder design (fail → escalate; cheap because most queries stop at rung 1)
1. **L0 Certified replay** (deterministic) → miss →
2. **L1 Governed metric / template** (slot-fill, deterministic compile) → slot/exec fail →
3. **L2 Verified free-form** — cyclic loop: gen N candidates → sqlglot allowlist → execute all → **result-set vote** → sanity
   rails. Disagreement/rail fail →
4. **L2′ Escalate compute** — bigger model and/or more samples (test-time compute), re-vote → still fail gate →
5. **L3 Abstain / clarify** — reject + suggest 2-3 answerable nearby questions.
Governing rules: **retry budget ≤2 escalations**; **a grader at every rung** (execution result > relevance/faithfulness >
calibrated confidence > LLM-judge); **verifier gate before delivery**; **never deliver unverified — abstain instead**;
side-effecting operations never auto-escalate, they block + notify. Every ladder decision is logged to the audit ledger.

### (e) Build first vs defer
**Build first (highest ROI, all VERIFIED patterns):**
1. The **cascade/fallback ladder as an explicit state machine** (LangGraph-style or a thin in-house cyclic runner over the
   existing `dag_runner`) — it *is* the adaptive router; graders + retry budget + abstain gate wired in.
2. **Difficulty/type classifier** at the front (operation/file/data type + easy/hard) to pick the starting rung (Adaptive-RAG idea).
3. **Default RAG stack** (contextual chunks + hybrid + reranker) for the doc corpus, with a labeled gold set measuring recall@k/nDCG + RAGAS faithfulness.
4. **Governed tool/skill registry** with constrained-decoding (`strict:true`) tool args, per-tool golden tests, and compliance-gate routing for side-effecting tools.
5. **Execution-grounded graders** (SQL executes/bounds; retrieval relevance; faithfulness) — reuse the NL2SQL sanity rails + result-vote.

**Defer until a concrete need appears:**
- **GraphRAG/LazyGraphRAG** — only when cross-contract global questions show up.
- **ColPali** — only for scanned/layout-heavy PDFs.
- **Tool-RAG / code-execution-MCP** — only past ~20 tools.
- **Multi-agent orchestration** — only for a genuinely parallel, breadth-first workload (15× cost otherwise).
- **Full planner (LATS/ToT)** — only for open-ended investigation where value justifies the compute.
- **Learned router (RouteLLM-style)** — start with rules + calibrated confidence thresholds; learn a router once you have logged traffic.

---

### Primary sources
- RAG patterns: Anthropic Contextual Retrieval https://www.anthropic.com/engineering/contextual-retrieval · agentic-RAG patterns https://www.brightter.com/articles/agentic-rag-five-retrieval-patterns-that-survive-production · CRAG arXiv 2401.15884 · Self-RAG arXiv 2310.11511 · Adaptive-RAG arXiv 2403.14403 · HyDE arXiv 2212.10496 · ColBERTv2 arXiv 2112.01488 · ColPali arXiv 2407.01449 (https://arxiv.org/html/2407.01449v6) · GraphRAG arXiv 2404.16130 · LazyGraphRAG https://www.articsledge.com/post/lazygraphrag-retrieval-augmented-generation · WildGraphBench arXiv 2602.02053
- Rerankers: https://futureagi.com/blog/best-rerankers-for-rag-2026/ · https://particula.tech/blog/reranker-models-compared-cohere-voyage-jina-bge-latency-ndcg
- Eval: RAGAS https://qaskills.sh/blog/ragas-rag-evaluation-metrics-complete-guide · https://langcopilot.com/posts/2025-09-17-rag-evaluation-101-from-recall-k-to-answer-faithfulness
- Orchestration: LangGraph https://www.folio3.ai/blog/langchain-vs-langgraph-ai-agent-framework · https://atlan.com/know/ai-agent/ai-agent-memory/what-is-langgraph/ · Anthropic multi-agent https://www.anthropic.com/engineering/multi-agent-research-system · Cognition/LangChain "when to build multi-agent" https://www.langchain.com/blog/how-and-when-to-build-multi-agent-systems · SDK compare https://composio.dev/content/claude-agents-sdk-vs-openai-agents-sdk-vs-google-adk
- Reasoning patterns: ReAct arXiv 2210.03629 · Reflexion arXiv 2303.11366 · Tree-of-Thoughts arXiv 2305.10601 · LATS arXiv 2310.04406 · https://dev.to/gabrielanhaia/react-plan-and-execute-or-reflection-the-three-agent-patterns-every-engineer-needs-in-2026-355p
- Routing/cascades: RouteLLM arXiv 2406.18665 · BEST-Route arXiv 2506.22716 · "Resample or Reroute?" arXiv 2607.08665 · "Cluster, Route, Escalate" arXiv 2606.27457 · UCCI arXiv 2605.18796 · https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades
- Tools/skills: MCP 2026 https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026 · code-execution-with-MCP https://www.anthropic.com/engineering/code-execution-with-mcp · Agent Skills https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills · agentskills.io · RAG-MCP arXiv 2505.03275 · ScaleMCP arXiv 2505.06416 · Tool-RAG https://next.redhat.com/2025/11/26/tool-rag-the-next-breakthrough-in-scalable-ai-agents/
- Structured outputs: https://collinwilkins.com/articles/structured-output · Guardrails AI https://orq.ai/blog/llm-guardrails
