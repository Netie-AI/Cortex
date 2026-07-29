```yaml
id: 2026-07-27_cursor_rag-retrieval-hardening
source: cursor
date: 2026-07-27
operator: jian-hong
prompt_used: skill_distill/prompts/ASK_CURSOR.md (ad-hoc RAG debug session)
distill_trace: skill_distill/DISTILL.md
status: normalized
airgpt_paths:
  - D:/AirGPT/rag/answer.py
  - D:/AirGPT/rag/goal.py
  - D:/AirGPT/rag/websearch.py
  - D:/AirGPT/index.html
```

# RAG retrieval hardening — AirGPT → Cortex handoff

## Raw answer (session summary)

AirGPT RAG (Demo multi-format + Good Good spaces) failed on compound generative asks:
inventory/summarize-all-files, then multi-goal person/paper/research questions.
Symptoms: resistor docs cited for Jian Hong / LeCun asks; web cites titled `+` and `hong`;
coverage math treated `summarise`/`tlel` as missing facts; `/search` was corpus-dump not web;
pipeline UI cluttered Build.

Cursor fixed AirGPT in-place. **Cortex must absorb the same contracts** wherever it
owns retrieval, web ingest, goal splitting, or grounded answering (Netie Engine CRM,
DMS secure path, agent web_search organ).

---

## What we built / fixed (chronological)

### 1. Hosting UI (AirGPT) — contrast + Cortex ensure
- Dark engine cards in light theme had dark ink → “blurry” Netie cards.
- Forced light ink on `.netie-box`; offline banner explains chat still works via keys.
- `POST /api/cortex/ensure` + Start Cortex button; health timeout 0.45s → 1.2s;
  `snapshot(fresh=True)` for Hosting.

### 2. `/search` meaning (critical UX)
- **Was:** `/search` → `ragRunSearch` = local hit dump only → JEPA wall for “who is jian hong”.
- **Now:** `/search` / `/web` = web-armed ask (ingest + answer). `/local` = corpus dump.
- Live DDG for “jian hong netie ai startup” already returned Crunchbase/LinkedIn — UI never called it.

### 3. Corpus inventory vs topical retrieve
- Ask “summarise what this space / files contain” must **list every source**, not retrieve.
- `wants_corpus_summary()` + early `answer_events` path → `summarize_space(force=True)`.
- Guaranteed numbered inventory even if LLM blurb drops sources.
- Pipeline UI collapsed under “Inspect pipeline · chunk samples” (hidden by default).

### 4. Coverage / goal math lying about typos
- Query: `summarise and tlel me what is the file about, ensure all thing sna dall files…`
- Goal assess treated `summarise`, `tlel`, `file`, `sna`, `dall` as **missing evidence** → 13% coverage → “corpus does not mention summarise”.
- **Fix:** expand `goal._STOP` + `answer._STOPWORDS` for ask-chrome; drop short low-vowel typo crumbs in `salient_terms`; safety-net redirect to inventory.

### 5. Multi-goal generative ask (this incident)
User ask (paraphrased): Jian Hong best people · JEPA vs transfer · cognition file · Yann LeCun papers (searhc) · get rich from RAG.

**Failures observed in answer:**
| Symptom | Root cause |
|---------|------------|
| Resistor pages cited | Demo space mixes resistors + cognition + JEPA; hybrid RRF on mega-query surfaces unrelated locals |
| Cite chips `+`, `hong` | Junk / truncated web goals (`+`, `hong`) → DDG Plus-sign / Hong Kong pages ingested |
| “500 Jian Hongs on LinkedIn” | Soft person query without Netie disambiguation; no entity goal |
| No LeCun papers | `searhc` typo → `wants_web=False`; goals didn’t extract “Yann LeCun papers” |
| JEPA “not in evidence” | Mega-goal truncate + local resistor noise drowned JEPA already in space |
| Get-rich answered from resistors/config | Off-topic locals still in context; no product/market evidence filter |

**Fixes shipped in `rag/answer.py`:**
1. `wants_web`: typo `searhc`/`serach`; `help me find` / `check who` / `what paper`.
2. `split_web_goals`: split on next/then/also; **entity-first goals** (Jian Hong Netie, Yann LeCun papers, JEPA vs transfer, cognition OpenStax); reject single-token junk; drop get-rich goals from web.
3. `_result_matches_goal`: DDG hit must share content tokens with goal (blocks +/hong).
4. `_filter_hits_to_goals`: drop local chunks that share no tokens with any goal (blocks resistors on LeCun ask).
5. Per-goal re-retrieve after web ingest instead of one mega-query retrieve.
6. `_display_path`: prettier cite names (`Yann LeCun` not `url_en-wikipedia-…`).

Verified goals for the compound ask:
```
- Jian Hong Netie AI founder
- Yann LeCun papers publications
- JEPA vs transfer learning Yann LeCun
- what is cognition psychology OpenStax
```
Filter keeps LeCun + cognition; drops resistor.

---

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Slash `/search` must mean web+ingest, not local dump | AirGPT UX bug; DDG already correct | high | Cortex agent `web_search` organ + RAG skill |
| Compound asks need **entity-first goal split** before retrieve | Mega-query → resistors; entity goals → clean | high | Cortex retrieval planner / orchestrator |
| Never search single tokens (`hong`, `+`) | DDG returns Hong Kong / Plus sign | high | web_search tool guard |
| Web hit must token-overlap goal before ingest | `_result_matches_goal` | high | ingest gate |
| Local hit must token-overlap ≥1 goal before cite | `_filter_hits_to_goals` | high | cite gate |
| Ask-chrome + typos are not “missing facts” | coverage 13% incident | high | goal.assess stop lists |
| Inventory asks ≠ topical retrieve | summarise-all-files path | high | space summary skill |
| Mixed demo corpora poison generative chat | Demo multi-format | high | space hygiene + per-goal filter |
| Chat transcripts must never be world-evidence | authority.py | high | already in AirGPT; keep in Cortex |
| Generative cool + 100% grounded = split goals, filter evidence, then LLM | session | med | answer contract |

---

## Action YAML (for Cortex / Claude Code)

```yaml
- id: cortex.rag.goal_split
  title: Entity-first multi-goal planner
  do: |
    Port split_web_goals + entity table into Cortex retrieval planner.
    Split on next/then/also/?/!. Prefer named entities over mega-strings.
    Reject goals with <2 content tokens or in {+, hong, hmm, bro}.
  tests:
    - compound ask yields Jian Hong / LeCun / JEPA / cognition goals
    - never emits "+" or "hong" as a search goal

- id: cortex.rag.web_gate
  title: Web result must match goal
  do: |
    Before ingest_urls, require title|url|snippet token overlap with goal.
  tests:
    - goal "jian hong" does not ingest Plus-sign Wikipedia

- id: cortex.rag.cite_gate
  title: Off-topic local filter
  do: |
    After hybrid retrieve, drop chunks with zero overlap vs any active goal.
  tests:
    - LeCun ask in mixed resistor space cites 0 resistor paths

- id: cortex.rag.web_intent
  title: Typo-tolerant web intent
  do: |
    Match searhc/serach; help me find; what paper; /search|/web.
  tests:
    - "searhc for Yann LeCun papers" → wants_web true

- id: cortex.rag.inventory
  title: Space inventory skill
  do: |
    Detect summarise/list-all-files/what-does-space-contain → list every source,
    do not topical-retrieve.
  tests:
    - typo-laden summarise-all-files returns N sources, coverage not based on "tlel"

- id: cortex.rag.assess_stops
  title: Ask-chrome stopwords in goal distance
  do: |
    summarise/file/ensure/mentioned/tell never appear in missing[].
    Drop short low-vowel typo crumbs from salient_terms.
```

---

## Problems we faced (detailed — for Claude Code)

1. **Two products, one brain split**  
   AirGPT owns RAG UI + `rag/*`; Cortex owns engine on :8010. Fixes only in AirGPT leave Cortex agents repeating the same retrieval sins via `web_search` / memory. **Must dual-write contracts.**

2. **“Accuracy” was measured on the wrong tokens**  
   Goal distance rewarded finding the words *in the user’s question chrome*, not answering the question. That produced fluent refusals that looked rigorous (“13% coverage”) while being wrong.

3. **Shared index without shared intent**  
   Demo multi-format is a kitchen sink (resistors, SNR lab, OpenStax cognition, JEPA URLs). Hybrid RRF is correct *per query* but wrong *per multi-goal session* unless you retrieve per goal and filter.

4. **Web ingest is sticky pollution**  
   Once `+` / `hong` pages are ingested, later answers keep citing them. Gate **before** ingest; optionally quarantine low-overlap sources.

5. **Slash vocabulary lied**  
   UI said `/search` for “retrieval-only” while users (and English) mean search the web. Align vocabulary with mental model or generative quality dies in the first turn.

6. **LLM cannot save bad evidence**  
   Grounded prompts still narrate whatever is in context. Filtering evidence is the product; “be accurate” in the system prompt is not.

7. **Generative + cool still required**  
   User does not want extractive dumps only. Desired shape: multi-section generative answer, each section tied to goal-filtered citations, missing goals named honestly (“no Netie-specific Jian Hong page yet — here are candidates”).

---

## Netie / Cortex implications

### Build now
- Port goal-split + web/cite gates into Cortex retrieval / agent `web_search` path.
- Add regression tests mirroring AirGPT `tests/test_rag_web_intent.py` + filter cases.
- Document operator hygiene: separate spaces for EE demos vs research people vs JEPA.

### Park (until)
- Auto “get rich / product” lane (market research pack) — needs separate tool, not RAG cite.
- Learned person resolution (Jian Hong → Netie founder) via CRM/profile graph when DMS live.

### Tests required
- Compound ask → 4 entity goals, 0 junk goals.
- Mixed space LeCun ask → 0 resistor citations.
- Inventory typo ask → full source list, no “missing: tlel”.
- `/search who is jian hong` → web ingest of Netie/Crunchbase-class URLs.

---

## Operator retry (AirGPT)

Hard-refresh, Demo multi-format or a clean space, then:

```
/search Jian Hong Netie AI founder
/search Yann LeCun papers Google Scholar
compare JEPA with transfer learning
explain the cognition OpenStax file in this space
```

Prefer **one goal per message** until Cortex planner ships; compound asks work better now but single goals stay sharpest.

---

## Citations

- distill: skill_distill/captures/2026-07-27_cursor_rag-retrieval-hardening.md
- code: `D:/AirGPT/rag/answer.py` (`split_web_goals`, `_filter_hits_to_goals`, `_result_matches_goal`, `wants_web`, inventory path)
- code: `D:/AirGPT/rag/goal.py` (ask-chrome stops, typo salient filter)
- mirror: `D:/AirGPT/docs/RAG_RETRIEVAL_HARDENING.md` (if present)
