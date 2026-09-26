# Handoff: parallel scale sessions (2026-09-26)

Read this before starting a ticket in a spawned session. It carries what the parent session learned (Cortex PR #235, DMS PR #313); the parent's scratchpad and user-level notes do not travel.

## Where things are
- **Cortex:** branch `claude/cortex-scale-agi-state-fkg2b6` (draft PR #235, CI green at 4999e4c).
- **DMS:** branch `claude/cortex-scale-agi-state-fkg2b6` in `Netie-AI/dms` (draft PR #313, CI green at f73b7ab).
- Build on those branch tips, not on `main`. Both carry work that main does not have yet.

## What changed on these branches (the parts you will trip over)
1. **env-direct model transport** (founder-directed).
   - `CORTEX_MODEL_TRANSPORT=env-direct` arms the one Cortex model layer (`CortexOS/integrations/freeroute.py`) from provider keys in the process env, sent through `CortexOS/integrations/direct_providers.py`.
   - Unset, OpenVault FreeRoute stays the only transport.
   - Every stamp says `NOT OpenVault FreeRoute (env-direct)`.
   - Hardened after a security review:
     - relayed/anonymous callers are refused;
     - the opt-in latches at first read;
     - child processes lose provider keys;
     - redirects are refused;
     - bodies are bounded;
     - partial keys are redacted;
     - the kill switch `CORTEX_FREEROUTE=0` wins.
   - Tests: `tests/test_freeroute_env_direct.py`, `tests/test_env_direct_hardening.py`, `tests/dms/test_insights_env_direct_envelope.py`.
2. **Insights under env-direct.** `/v1/insights` generate requires an engine-auth-port caller (viewer+, e.g. `X-API-Key`). Anonymous callers get 401 with zero provider calls.
3. **Crew chain** (`CortexOS/crew/config.py`, `llm.py`, `connectors.py`, `keys.py`) spends `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `MISTRAL_API_KEY` and `CEREBRAS_API_KEY` via litellm. Crew also gained web_search/web_fetch (public-only, SSRF-guarded), a safe AST `calc` tool, and a sturdier tool loop.
4. **429 cooldown routing, per-question pin, certified definitions** in the SQL prompt; a warehouse column check on generated SQL; an ontology drift test.
5. **Follow-ups** (CX-MT-01), **empty-result self-correction** (CX-SC-01), **few-shot Text2SQL** (CX-FS-01).
6. **DMS:**
   - the Insights timeout is configurable (default 60 s);
   - the no-model ranking fallback runs on timeout;
   - ranking answer shapes are fixed;
   - rule-based chart recommendation ships as a Vega-Lite spec on the envelope; there is never a chart on an abstain (E14).

## Founder overrides (do not undo, do not widen without asking)
- **#215 dual-write guard.** The founder allowed diffs to:
  - `CortexOS/crew/config.py`, `crew/llm.py`,
  - `CortexOS/dms/answer_engine.py`, `dms/l2_generation.py`,
  - `packs/dms/generative/sql_generator.py`, `l2_adapter.py`.

  The guards are in `tests/test_crew/test_cot_climb.py` and `test_prompt_harness_climb.py`. Any other guarded file still needs a founder decision; ask, do not edit the guard.
- **DMS:** the founder allowed fixing DMS directly without routing through the prd-agent for this run.

## Provider keys in the environment (never print, log or commit values)
Status as measured 2026-09-25/26:

| Env var | Live status | Working model |
|---|---|---|
| `GEMINI_API_KEY` | works; free tier about 20 req/day on 3-flash, then 429 | `gemini-3-flash-preview` (2.5-flash is closed to new users) |
| `NVIDIA_API_KEY` | works; per-minute 429 under load; sometimes empty replies | `moonshotai/kimi-k3` (llama-3.3 is end-of-life) |
| `MISTRAL_API_KEY` | 429 rate limited | none |
| `CEREBRAS_API_KEY` | 402 billing | none |

## Non-negotiables (from CLAUDE.md and the founder's rules)
- **Answer-path tests** assert the rendered answer text and the rows, plus the DMS envelope (`assert_envelope_valid`). A green badge on abstention prose is a P0.
- **Every new test must fail on the base.** Prove it by stashing the production change. A skipped test is a failing test.
- **Never weaken a refusal or gate** to get green. If a guard blocks you, report it.
- **Stage explicit paths.** Never `git add -A`. Read exit codes directly; never judge through `| tail`.
- **`contract/` is never hand-edited.** An additive wire field is a contract minor plus `scripts/export_openapi.py` in the same commit.
- **Findings** go to `docs/subagents_findings/YYYY-MM-DD_<topic>.md` and `INDEX.md` (expected vs actual, repro, root-cause class, invariant).

## Measured state to beat
The live 52-question curated_ceo run through DMS gave:
- **unarmed:** 43/52 answered, WRONG 0 (was 28 / 15 WRONG);
- **armed:** 43/52 answered, WRONG 0, but only 4 came from model SQL. Provider 429s dominate.

Crew live eval:
- NVIDIA kimi-k3: 11/15.
- Gemini: 9/15. All 6 fails were 429.

## Session workflow
1. Branch from the tip above into your assigned branch.
2. Build with tests that fail on the base.
3. Run the full suite.
4. Push.
5. Open a **draft PR whose base is the parent branch above** (not main), so the parent can integrate.
6. File a finding.
7. Say in the PR body what was verified and what was assumed.
