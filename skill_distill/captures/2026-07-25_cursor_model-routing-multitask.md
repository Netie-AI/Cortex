```yaml
id: 2026-07-25_cursor_model-routing-multitask
source: cursor
date: 2026-07-25
operator: cursor-agent (distill-session follow-on)
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: normalized
lanes: [multi_agent_coord, model_routing, context_precision]
cross_ref:
  - skill_distill/captures/2026-07-25_cursor_distill-session.md
  - skill_distill/captures/2026-07-25_claude-code_all-lanes.md
  - Downloads/netie-distill-master.md (Claude Cowork / Agent SDK)
  - Downloads/2026-07-25_claude-app_capabilities.md
  - https://cursor.com/docs/subagents
  - https://cursor.com/docs/cloud-agent
  - https://cursor.com/docs/agent/tools
```

## Raw answer

### How Cursor coordinates multi-agents

**Invocation surfaces** (`evidence: docs` https://cursor.com/docs/subagents + `observed` Task tool schema, `confidence: high`)

| Surface | Mechanism |
|---------|-----------|
| Parent Agent | Multiple `Task` tool calls in **one assistant turn** → parallel children |
| Natural language | “run explore + shell in parallel”, “use the verifier agent” — parent matches intent to `subagent_type` / custom agent `description` |
| Explicit slash | `/<agent-name>` or “use the verifier subagent” |
| Background / Multitask | `run_in_background: true` (required when Multitask Mode UI is on); completion via end-of-turn notification — do **not** busy-poll |
| Resume | `resume: <agentId>`; `interrupt: true` only if user wants to cut a running child |
| Cloud | `environment: cloud` or `/in-cloud` / Cloud Agents UI; child gets own VM + branch |
| Long-running cloud | `/babysit` / cloud agent follow-ups |

**Isolation contract** (`evidence: observed` Task tool instructions + `docs` subagents, `confidence: high`)

- Child does **not** inherit parent user message or prior assistant tool history.
- Parent **must** put full goal + constraints + file paths into `prompt`.
- Default: same machine / shared filesystem for `environment: local`.
- Optional: git worktree isolation (`best-of-n-runner` / worktree patterns); cloud = separate checkout.
- Return: **summary/final report only** into parent context (same summary-only pattern as Claude Code Agent tool / Cowork Workflow) — `evidence: observed` + Claude master distill cross-ref, `confidence: high`.

**Built-in vs custom agents** (`evidence: docs` subagents, `confidence: high`)

| Kind | Role | Model bias (docs) |
|------|------|-------------------|
| Explore | Search/answer codebase Qs | Faster model |
| Bash | Shell specialist | Domain-tuned |
| Browser | Browser automation | Domain-tuned |
| Custom `.cursor/agents/*.md` | Project-defined | `model: inherit` or explicit ID |
| User `~/.cursor/agents/` | Global custom | Same frontmatter |

Also observed in this harness (may be product-specific wrappers): `generalPurpose`, `shell`, `cursor-guide`, `ci-investigator`, `bugbot`, `security-review`, `best-of-n-runner` — `evidence: observed`, `confidence: high` for this Cursor Agent runtime; treat as Cursor-internal Task types layered on docs’ “custom + built-in” model.

**Nested subagents** (`evidence: docs` subagents “Nested Subagents” + SDK notes, `confidence: med`)

- Allowed when child has Task/Agent tool access and hooks/policies don’t block.
- Docs: enable via Settings → Subagents → Enable Nested Subagents (not verified in this UI session).
- SDK / forum: nesting often **top-level + one child depth**; grandchildren may be blocked — **UNKNOWN exact Cursor Desktop depth** — park experiment.

**Hooks / governance on spawn** (`evidence: docs` hooks + subagents, `confidence: med`)

- `subagentStart` / `subagentStop` (and related) can audit or block spawn — same industry pattern as Claude Code PreToolUse / SubagentStart.
- Netie: map to hook gate before `AGENT_TASK` / DAG fan-out.

---

### How Cursor chooses models (including “normal text”)

**Three layers** (`evidence: observed` + `docs` subagents model syntax, `confidence: high` for layers 1–2; `med` for layer 3)

1. **Parent / chat model** — whatever the user selected in Cursor chat (Composer, Claude, GPT, Grok, etc.). This agent identifies as “Cursor Grok 4.5” in-session — `evidence: observed`, `confidence: high` for this session only.
2. **Subagent frontmatter `model:`** — `inherit` (default) | concrete model ID string in `.cursor/agents/*.md`.
3. **Task tool `model` parameter** — optional override when launching a child; **must** be from harness allowlist. Unknown slug → **do not substitute**; tell user which models are available — `evidence: observed` Task schema, `confidence: high`.

**Allowlist observed this harness (2026-07-25)** (`evidence: observed`, `confidence: high` for this build; product may change)

- `claude-opus-4-8-thinking-high`
- `composer-2.5`
- `composer-2.5-fast`
- `cursor-grok-4.5-high`
- `gpt-5.6-sol-medium`

**Natural-language → model** (`evidence: docs` subagents examples + `inferred` parent routing, `confidence: med`)

- Docs examples of parameterized IDs: `composer-2.5[]`, `[fast=false]`, `claude-opus-4-8[effort=high]`, `[context=300k]`.
- User says e.g. “use Composer for explore, Opus for review” → parent Agent maps intent to Task `model` / custom agent file with that `model:` field.
- There is **no** separate free-text model router API exposed to the parent beyond Task `model` + agent YAML + chat picker — routing is **prompt + schema constrained**.

**Composer vs frontier** (`evidence: docs` + product positioning, `confidence: med`)

| Class | Typical use (docs / practice) |
|-------|-------------------------------|
| Composer / Composer Fast | Cheap/fast explore, mechanical edits, high-volume Task fan-out |
| Frontier (Claude Opus, GPT, Grok high) | Hard reasoning, security review, final synthesis |
| `inherit` | Child matches parent chat model — safest default |

**Fallback when requested model blocked** (`evidence: docs` subagents, `confidence: high`)

- Team admin model controls, legacy Max Mode filters, plan limits → Cursor falls back (docs); exact fallback chain server-side **UNKNOWN**.

**vs Claude Code / Claude.app** (`evidence: cross-ref` all-lanes + Downloads Claude.app capabilities, `confidence: high` for contrast)

| | Claude Code / Cowork | Cursor |
|--|----------------------|--------|
| Default child model | Often parent / frontmatter `model` | `inherit` or Task allowlist slug |
| Explicit per-child model | Frontmatter + Agent tool params | Task `model` + agent YAML + NL mapping |
| Model as product surface | Anthropic family (+ aliases) | Multi-vendor + Composer |
| Netie opportunity | Single provider | **First-class routing table** (composer vs frontier vs cost) |

---

### Context understanding, retrieval, and action precision

**Why actions stay precise** (`evidence: observed` system instructions + tools, `confidence: high`)

1. **Stacked durable instructions** — user rules, project `.cursor/rules`, `AGENTS.md`, skills (`description` match → load SKILL.md), always_applied workspace rules. Same progressive-disclosure pattern as Claude Skills.
2. **Mandatory tool discipline** — Grep/Read/Glob before inventing paths; cite real files; MCP: `GetMcpTools` **before** `CallMcpTool` (lazy schemas).
3. **Mode constraints** — Plan/Ask = no writes; Agent = execute; Debug = evidence-first.
4. **Child prompt engineering** — isolation forces parent to write a self-contained brief (goal, paths, success criteria, “return X”) — reduces hallucinated shared context.
5. **Skills auto-trigger** — YAML `description` intent match (not only slash) — same as Claude Code skills.
6. **No silent repo embedding RAG** in-agent by default — retrieval is **agentic search** (Grep/semantic search tools / Explore). Embeddings optional via MCP or product index — `evidence: observed` for agentic search; `inferred` for background codebase index, `confidence: med`.

**Context window hygiene** (`evidence: observed` + `inferred`, `confidence: med`)

- Large tool results / files → read slices, not whole dumps.
- Subagent summary-only return protects parent window (same as Claude Code).
- Compaction / summarization of long chats — product-side; exact Cursor compaction survive/drop table **UNKNOWN** vs Claude Code’s documented PreCompact/PostCompact.

**Cloud agent context** (`evidence: docs` cloud-agent + `observed` Task environment:cloud, `confidence: high`)

- Fresh clone / own branch; **MCP from team cloud config** (cursor.com/agents), not local MCP — do not assume local Excel/browser MCP on cloud child.
- Secrets: cloud dashboard / team secrets, not parent machine env by default.

---

### Netie / Cortex mapping (build vs park)

| Cursor behavior | Netie / Cortex | Promote |
|-----------------|----------------|---------|
| Parallel Task fan-out + full child prompt | `dag_runner` / workflow_templates fan-out; mandate brief payload | **rule** |
| `model` allowlist + inherit | Routing table: composer/fast vs frontier vs cost; reject unknown | **skill** + routing config |
| Explore-on-fast / review-on-frontier | Default agent profiles in `prompt_library` / AGENT_TASK annotations | **skill** |
| GetMcpTools before CallMcpTool | Already: discovery / lazy MCP — keep | **none** (reinforce rule) |
| Summary-only child return | AGENT_TASK result contract = final report only | **rule** |
| Nested depth gate | Cap spawn depth (Claude Code: env max 5) | **parking** until product policy |
| Cloud MCP ≠ local MCP | P17 portable + team connector registry | **parking** P17 |
| NL model pick | Intent → routing table (don’t invent models) | **skill** |

---

### Experiments (parked until run)

| ID | Experiment | Home |
|----|------------|------|
| C1 | Spawn Explore with `composer-2.5-fast` + Opus review Task; diff quality/latency | `tests/` or manual |
| C2 | Omit context from child prompt; confirm failure mode | distill capture addendum |
| C3 | Request invalid Task `model` slug; confirm refuse-no-substitute | harness |
| C4 | Nested Task from child; measure depth limit | Settings Nested Subagents |
| C5 | Cloud Task + local-only MCP; confirm connector miss | cursor.com/agents |

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Parallel multi-agent = multiple Task calls in one parent turn | observed + docs | high | rule |
| Child context isolated; parent must pass full prompt | observed | high | rule |
| Child returns summary only into parent | observed | high | rule |
| Custom agents live in `.cursor/agents/*.md` (+ `.claude/`/`.codex/` compat) | docs | high | skill |
| Frontmatter: name, description, model, readonly, is_background | docs | high | skill |
| Task `model` must be allowlisted; no silent substitute | observed | high | rule |
| Observed allowlist includes composer-2.5, composer-2.5-fast, claude-opus-4-8-thinking-high, cursor-grok-4.5-high, gpt-5.6-sol-medium | observed | high | parking |
| NL can select model by instructing parent to set Task model / agent file | docs + inferred | med | skill |
| Composer/fast for explore; frontier for hard reasoning | docs + inferred | med | skill |
| Model fallback when blocked (admin/plan/Max Mode) | docs | high | parking |
| Built-in Explore uses faster model | docs | high | skill |
| Nested subagents optional / settings-gated; exact depth UNKNOWN | docs + unknown | med | parking |
| MCP lazy: GetMcpTools before CallMcpTool | observed | high | rule |
| Cloud Task MCP from team config not local | docs + observed | high | parking |
| Precision = rules+skills+agentic Grep/Read + mode + child brief | observed | high | rule |
| No default chat-RAG over full repo; agentic retrieval | observed + inferred | med | none |
| Cursor multi-vendor routing is differentiator vs Claude Code single-family | cross-ref | high | skill |

## Action YAML

```yaml
- action: spawn_parallel_agents
  when: "user asks multitask / parallel explore+shell / multiple independent lanes"
  uses: [Task]
  inputs: [goal_per_lane, full_context_brief, subagent_type, run_in_background?]
  outputs: [agent_ids, notifications]
  netie_equivalent: CortexOS/execution/dag_runner.py + workflow_templates fan-out
  distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md

- action: select_child_model
  when: "user names Composer/Opus/GPT/Grok OR cost/speed tradeoff OR Task model param"
  uses: [Task.model | agent frontmatter model]
  inputs: [requested_model_or_class, allowlist]
  outputs: [resolved_slug | user_error_if_unknown]
  netie_equivalent: routing table (composer|frontier|cost) on AGENT_TASK annotations
  distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md

- action: brief_isolated_child
  when: "any Task/subagent spawn"
  uses: [Task.prompt]
  inputs: [goal, paths, constraints, success_criteria, return_shape]
  outputs: [self_contained_prompt]
  netie_equivalent: prompt_library + AGENT_TASK payload must be complete
  distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md

- action: call_mcp_lazy
  when: "external connector needed"
  uses: [GetMcpTools, CallMcpTool]
  inputs: [server, toolName?, pattern?]
  outputs: [schema_then_result]
  netie_equivalent: discovery lazy MCP catalogs
  distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md

- action: retrieve_then_act
  when: "codebase question or edit"
  uses: [Grep, Glob, Read, Explore Task]
  inputs: [query, globs]
  outputs: [cited_paths, grounded_edit]
  netie_equivalent: web_tools + RawKnn optional MCP — not silent blob RAG
  distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md
```

## Netie implications

- **Build now:**
  1. Rule: parallel spawn + **mandatory child brief** + summary-only return.
  2. Skill: **model routing table** (composer/fast vs frontier) + refuse unknown models.
  3. Default profiles: explore→fast; review/security→frontier.
  4. Keep lazy MCP discovery; document cloud connector registry ≠ local.
- **Park (condition):**
  - Nested depth product policy (unpark when C4 measured).
  - Exact Cursor compaction survive/drop (unpark when docs/UI confirmed).
  - Server-side model fallback chain (unpark when admin docs published).
  - Full cloud MCP parity (P17).
- **Tests required:** C1–C5 above; Playwright optional for cloud UI.

## Citations

- distill: skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md
- distill: skill_distill/captures/2026-07-25_cursor_distill-session.md
- distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md
- https://cursor.com/docs/subagents
- https://cursor.com/docs/cloud-agent
- https://cursor.com/docs/agent/tools
- Downloads/netie-distill-master.md
- Downloads/2026-07-25_claude-app_capabilities.md
