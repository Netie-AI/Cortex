```yaml
keywords: [false-green, import-linter, eval-corpus, manifest-bypass, wasm, auth-default, cortex_contract, module-identity, doc-sprawl, plane-taxonomy, osr, gen_cfsm, space-acl]
main_idea: "Three gates report green while structurally unable to fail, the one rigorous security control is bypassed on the product path, and cortex_contract resolves as two module identities - meanwhile the docs disagree with each other on test count, branch, gate and whether WASM exists."
models: [opus]
workflow: netie-ground-truth-audit (7 survey agents + 3 adversarial verifiers)
reuse: golden_rule
status: verified
cite: task: w7wgsou5r | agent: doc-census,core-execution-reality,capability-surface-reality,safety-isolation-audit,repo-topology,ops-layer-inventory,honest-backlog + verify:core,verify:safety,verify:topology
repo: multi
date: 2026-08-01
```

# Estate audit - false greens, module identity, and what Cortex actually is

## Main idea

- Three gates report green while being structurally unable to fail: the C2 import
  boundary (blind on the hot path + cache-dependent verdict), the eval corpus (zero
  detection rate on the last five live P0s), and the "live" corpus mode (scores offline).
- `enforce_manifest` is real, rigorous and **bypassed on the product path**:
  `answer_engine.answer` takes `verified` as optional and `POST /dms/query` calls it
  without one.
- `cortex_contract` and `packages.cortex_contract` are **two module identities of one
  file**. `canonical_manifest_bytes` already takes the wrong `isinstance` branch; bytes
  match today only because every Manifest field happens to be a string.
- Cortex is a governed NL-to-SQL answer plane plus a thin real orchestrator - **not** an
  LLM serving engine and not the three-tier OSR/race/gen-cFSM stack the docs imply.
- README tells customers the system "applies Wasm sandboxing". It does not; both modules
  are 0-byte and the wrapper has zero production callers.

## Keywords (search)

`false-green`, `import-linter cache`, `__init__.py invisible`, `eval corpus blind`,
`offline scored as live`, `manifest bypass`, `verified optional`, `module identity`,
`isinstance False same file`, `wasm 0-byte`, `DMS_AUTH_DISABLED`, `osr.route no caller`,
`gen_cfsm unreachable`, `demo_acl`, `plane taxonomy`

## Questions left open

- C7 fork: is 17-confidently-wrong a calibration problem (days) or a capability problem
  (not this quarter)? Not answerable without real customer questions.
- Which of the 6 demo tables are company-scoped vs Space-scoped? Product decision, blocks
  the ACL wiring.
- Does `query_skill` (317 stored, 0 retrievals across 120 questions) get deleted or
  proven with a number?

## Full answer / evidence

### 1. The three false greens

**Import boundary is blind on the hottest path.** `lint-imports` reports 2 kept / 0
broken. `packs/dms/semantic/` has no `__init__.py`, so it is invisible to the grimp
graph. Creating one surfaces 8 real violations of hard invariant #1 in
`CortexOS/dms/answer_engine.py` (-> `packs.dms.semantic.loader` at l.61, 755, 1405,
1473; `query_skills` l.1325; `values` l.176, 259). Proved by experiment and reverted.

Worse, the verdict is **cache-dependent**: identical tree, `.grimp_cache` cleared ->
"2 kept, 0 broken"; `.import_linter_cache` also cleared -> "1 kept, 1 broken". R-0007.

`CLAUDE.md:41` instructs agents that `.importlinter` "is the stricter check, and the one
to trust". That is backwards - the AST test is the one that knows.

Separately: `.importlinter` carries **30 explicit ignore entries** covering most of the
API layer plus `dag_runner`, `tool_runner`, `agent_sdk.sdk`, `ontology.registry`,
`ponytail.middleware`. "The engine only ever holds a port" is true of two modules.

**The eval corpus has a demonstrated zero detection rate.** The last five commits
(`78309fc`, `8f601ea`, `dc86689`, `2475f50`, `ebd049b`) are five confidently-wrong answer
defects found by a human in one live session. Every commit body records the corpus stayed
376/376 wrong=0 throughout. Three corpus items have silently flipped answer->abstain
since 2026-07-31 and scored free, because the gate asserts only `wrong == 0`.

**The "live" mode is offline.** `bench/corpus.py:295-328` checks the envelope for
`abstained`/`sql_used` presence, then calls `score_item()` which re-runs the question
offline against the local warehouse and returns *that*. `corpus_live_1b.json` claims
`mode=live`. This is exactly the intermediate-artifact verification CLAUDE.md section 8
exists to prevent (R-0001).

**Claim bound:** `verify_gold --status` -> `seeds=47 paraphrases=329 (verified 0)
expanded_n=376 claim_n=47`. Under R-0010 that bounds error at 6.4 percent, not <1 percent.

### 2. Manifest bypass on the product path

`CortexOS/execution/manifest.py` is rigorous and fail-closed (34 raise sites; hostile
corpus 90 cases). It is wired into production through `submit.py:189` on `/v1/contract/*`.

But `answer_engine.py:1316` takes `verified: VerifiedManifest | None = None`, and the
`else:` branch at `:1650` calls `get_connection(DEFAULT_DB, ...)` at `:1655` with only
`guard_and_execute`. `query_service.py:957` calls `_engine_answer(...)` with no
`verified`. **`POST /dms/query` - the demo and product path - runs SQL under no manifest
enforcement.**

Fix: make `verified` required. Highest security value per hour available.

### 3. Two module identities of one file

Proved in-process:
```
cortex_contract.execution.Manifest is packages.cortex_contract.execution.Manifest -> False
```
Both resolve to `D:\Cortex\packages\cortex_contract\execution.py`.

`manifest.py:47-50` try/except prefers the bare spelling; 16 tracked files import
`packages.cortex_contract.*` unconditionally. Inside `canonical_manifest_bytes`,
`execution.py:82` does `isinstance(manifest, Manifest)` - in `drillthrough.py:290-301`
that evaluates **False** and silently takes the `else: dict(manifest)` branch.

Bytes match today (both sha `e6bde24bd9d80fb5`) by luck: every field is
str/list/dict/None, so `dict(model)` and `model_dump(mode="json")` coincide. Add one
datetime, UUID or nested model and drillthrough token hashing diverges from manifest
verification - presenting as a crypto bug, exactly as CLAUDE.md:74-79 warns.

**This must be fixed before any contract-wheel packaging work.** A real installed wheel
turns a latent divergence into a live one.

### 4. What Cortex actually is (corrected)

Not an LLM serving engine: no `chat/completions` implementation anywhere in `CortexOS`.
Tokens are bought via litellm (self-hosted vLLM default `http://127.0.0.1:8000/v1`) or
OpenVault FreeRoute.

**(a) A governed NL-to-SQL answer plane.** Real, and the product.

**(b) A thin agentic orchestrator.** One runner, `dag_runner.run_dag`, 10 node kinds,
`parallel=False` by default. Only `workflow_runner.py:450` passes `parallel=True`, and
that path is genuinely parallel and genuinely LLM-backed.

**Automatic architecture selection: one live selector, not three.**
- LIVE: `race_router.auto_route` behind `POST /api/engine/auto`. Requires cosine >= 0.80
  against a family centroid AND a stored winner with >= 3 runs; otherwise probes top-3
  concurrently, scores predicates-over-judge, re-runs the winner at scale.
- LIVE, undocumented: `workflow_recognizer.recognize` picks the workflow template for
  `POST /api/workflows/run`.
- **CODE-ONLY: `osr.route`.** Zero production callers. `POST /api/engine/osr` is
  classify-only by its own docstring; `/fire` classifies then dispatches elsewhere.
- **CODE-ONLY: `gen_cfsm`.** No HTTP entry. Cannot enter a race pool -
  `COLD_START_ORDER = ("minimal","sequential","dag")` and `auto_route` never passes
  candidates. `generate_ir` is a deterministic chain; no model writes a plan today.
- **JEPA is a name, not an artefact.** The family gate is a sha256 feature-hash into 64
  buckets. `action_value.py:4-5` says so explicitly.

**(c) C7 shipped as a fallback BEHIND the cascade, not as a replacement.**
`route_to_metric` runs first (39 `re.search`, 37 `return MetricPlan`); the L2 block does
not start until `:1513`, gated on `DMS_L2_ENABLED` (default off) plus an OpenVault ping
plus the leave-machine gate. Measured with it on: 17 confidently wrong vs a floor of 0.

**Ungoverned tool path:** `agent_task.default_broker:90` checks `web_tools` (:95) and
discovery (:101) *before* falling through to governed F8 (:105), so `web_search` and
`web_fetch` reach the network with no allowlist, sanitizer, compliance or ledger.

**Action registry reality:** 25 entries, 24 are ledger event types, **1 is invocable** -
`export_pptx`. "Actions are the only write path" describes a path with one action.

### 5. Security posture

- WASM: both modules 0 bytes (git empty blob). `wasm_isolate.py` real but zero production
  callers. `README.md:9` claims it publicly; every internal doc is honest.
- No container isolation. `app_runner.py:145` `subprocess.Popen` on host with
  `env = os.environ.copy()` - child inherits every engine secret. Both Dockerfiles run as
  root, no `cap_drop`/`read_only`/`seccomp`/limits, bind `0.0.0.0`.
- `DMS_AUTH_DISABLED=1` in both images -> `Caller(role="admin", actor="auth_disabled")`.
  Fallback is three demo keys published in-tree, applied silently (R-0011), and
  `scripts/secrets_scan.py:67-68` is hardcoded to skip them.
- All 11 `/api/apps/*` routes have zero `Depends`. So does `POST /run`, `dms_query`,
  `chat_routes`. `require_role` is broadly used but strictly opt-in per module, and is
  missing from exactly the modules that execute code or run SQL.
- Mitigation credited: `CORTEX_PROFILE=core` makes the exec chain return 501. The live
  host-exec surface is the `-full` image only.

### 6. Repo topology - do not split

`git ls-files activeflow` -> **0** (gitignored). Total tracked **826**. `.git` ~305 MB.
`core.longpaths=true`, longest tracked path 73 chars. No LFS, no submodules in Cortex.
Partial clone unused - `--filter=blob:none` is the free win.

DMS boundary is genuinely enforced by `tests/invariants/test_boundaries.py` (AST), but
`PYTHON_ROOTS` covers only `apps/api` and `packages` - `scripts/` and `tests/` are
unscanned, and two "we never import CortexOS" files live there.

Undeclared reverse edge: `bench/corpus.py:210` and `bench/live_probe.py:29` import
`dms_executor` (Cortex -> DMS). CLAUDE.md forbids only DMS -> Cortex.

### 7. Doc state

11 contradictions found. Highlights: three files say 153 tests (measured 1289); three say
F7 is in progress (it passed 2026-07-22); `CLAUDE_HANDOFF.md` names branch `dms-v2`
(retired per `ARCHITECTURE.md:126`); `STATUS.md` header says G2.3 SHIPPED while `:442` in
the same file instructs the next agent to build G2.3; `docs/DEMO.md:6` starts with
`cd C:\Users\user\RUMA\Cortex`. Four copies of `PRODUCT_ROLES.md` say "keep this file
identical" and all four differ.

## Golden rule (if reusable)

**A gate must be proved able to fail on the specific defect class it claims to cover -
not merely able to fail in principle.**

`lint-imports` could fail, and did fail on other contracts, while blind to 8 violations on
the hot path because a package lacked `__init__.py`. The corpus could fail, and did fail
historically, while catching zero of five live P0s because it tests questions and the
defects were conversations.

Procedure when adding or trusting a gate:
1. Name the defect class in one sentence.
2. Write a deliberately-broken commit that exhibits *that class*.
3. Confirm the gate goes red on it, from a cold cache.
4. Revert. Record the proof in the gate's docstring.

Corollary: a coverage mechanism that depends on filesystem discovery (packages, globs,
test collection) must assert its own denominator. `lint-imports` should fail if the
analysed-file count drops.

Cross-ref: R-0001 (assert the customer artifact), R-0007 (verify a gate can fail),
R-0010 (rule of three).
