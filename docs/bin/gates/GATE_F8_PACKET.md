# Gate F8 — Tool-Call Execution (Sandboxed Actions)
**Date:** 2026-07-03 | **Branch:** dms-v2 | **Verdict:** not started — blocked on F5 gate + F6 + F7 remainder

## Scope
Wire `TOOL_CALL` DAG nodes through a governed execution path: wasm sandbox, compliance pre-check, security guards on params/outputs, actor-scoped output dirs, and F1 ledger audit. First concrete action: **PPT export** (demo-visible, small blast radius).

This is **not** a parallel spine — it extends the existing F1→F5 loop. LLM still extracts; rules still decide; actions only run after F5 pass + explicit human confirm where required.

## Prerequisites (do not skip)
| Gate | Why |
|---|---|
| **Gate F5 PASS** | Tasks must be compliance-gated before any action executes |
| **F6 shipped** | Skill cards (`required_tools`, `required_network`) scope what a tool call may do |
| **F7 remainder** | API-key auth + RBAC (viewer / steward / admin) + Postgres RLS on ledger reads |

Optional **vertical slice** (demo only): stub PPT export behind steward role + ledger logging, without full Postgres RLS — acceptable for internal demo, not for prod.

## What already exists (do not rebuild)
| Piece | Location | Gap |
|---|---|---|
| DAG runner (4 node kinds) | `CortexOS/execution/dag_runner.py` | `TOOL_CALL` → `UnsupportedDAGNodeKind` |
| DSL `tool_name` field | `CortexOS/fabrication/dsl_parser.py` | No executor |
| WASM sandbox | `CortexOS/execution/wasm_isolate.py` | Not called from DAG runner |
| Compliance engine | `CortexOS/compliance/engine.py` | Used for `deterministic_rule` + F5 gate only |
| Security guards | `packs/dms/security/` (`injection_guard`, `pii`, `scam_guard`) | Inbound messages only — not tool params/outputs |
| Skill capability model | `CortexOS/fabrication/skill_registry.py` | `required_tools`, `required_network` unused by executor |
| Actor-tagged ledger | `packs/dms/audit/ledger.py` | No `action.tool_call` events yet |
| Audit read API | `GET /dms/audit` in `CortexOS/api/dms_query.py` | No `?actor=` filter, no RBAC |

## Build exactly this
1. **`execute_tool_call_node()`** in `dag_runner.py`
   - Resolve `node.tool_name` against skill registry allowlist
   - Pre-check params via `ComplianceEngine` ruleset (`packs/dms/compliance/tool_call_rules_v1.yaml`)
   - Sanitize inbound params: `injection_guard` + `pii.redact_for_prompt` on string fields
   - Run tool inside `WasmSandbox` (or host shim for PPT when WASM module not ready — document escape hatch)
   - Writes **only** to `outputs/<actor>/<run_id>/` (create dir; deny all other FS paths)
   - Append ledger: `event_type="action.tool_call"`, `actor`, tool name, params hash (not raw secrets), output path, compliance verdict

2. **F7 remainder — auth middleware** (`CortexOS/api/`)
   - Inbound API keys scoped: `viewer` (read), `steward` (run gated actions), `admin` (audit + config)
   - Protect `POST /dms/run` (DAG) and new `POST /dms/actions/{tool}` shortcut
   - `GET /dms/audit?actor=&from_seq=&limit=` — admin/steward only; RLS-scoped when Postgres DSN set

3. **First tool: `export_pptx`**
   - Input: chart/table payload from Brain or warehouse query (read-only upstream)
   - Output: `.pptx` under `outputs/<actor>/<run_id>/export.pptx`
   - `requires_confirm=True` in task/skill card; F5 gate must pass before invoke
   - Demo: Brain or Chat "export to PowerPoint" triggers the path end-to-end

4. **Tests** (`tests/dms/test_tool_call.py`, `tests/execution/test_tool_call_node.py`)
   - Tool not in allowlist → blocked + ledger `action.tool_call_denied`
   - Compliance fail → no file written + ledger verdict fail
   - PII in params → redacted before execution; raw PII never in ledger payload
   - Sandbox write outside `outputs/` → denied
   - Successful run → file exists + ledger chain intact (`verify_chain()`)
   - RBAC: viewer cannot invoke; steward can invoke allowed tool; admin can filter audit by actor

## Test evidence (fill on ship)
```
pytest tests/dms/test_tool_call.py -q          → N passed
pytest tests/execution/test_tool_call_node.py -q → N passed
pytest tests/dms/test_security.py -q           → incl. RBAC + rate limit (F7 remainder)
pytest -q                                        → baseline green + new tests
```

## Gate F8 checklist
- [ ] `TOOL_CALL` nodes execute without `UnsupportedDAGNodeKind`
- [ ] Tool allowlist enforced via skill registry `required_tools`
- [ ] Compliance pre-check deterministic (same params 100× → identical verdict)
- [ ] Params sanitized (`injection_guard` + PII redact on strings)
- [ ] Outputs land only in `outputs/<actor>/<run_id>/`
- [ ] Every invocation appends `action.tool_call` (or `_denied`) to F1 ledger with `actor`
- [ ] `GET /dms/audit?actor=X` returns filtered chain (admin role)
- [ ] API-key middleware: viewer read-only; steward run; admin audit
- [ ] `export_pptx` demo path works from UI with F5 green verdict
- [ ] `pytest -q` green; `verify_all.ps1` green

## Files expected (on ship)
- `CortexOS/execution/dag_runner.py` — `execute_tool_call_node()`
- `CortexOS/execution/tool_runner.py` (or `packs/dms/actions/`) — host shim + wasm bridge
- `packs/dms/compliance/tool_call_rules_v1.yaml`
- `packs/dms/actions/export_pptx.py`
- `CortexOS/api/auth_middleware.py` (or `packs/dms/security/api_auth.py`)
- `CortexOS/api/dms_query.py` — audit filter + RBAC hooks
- `packs/dms/sql/00N_tool_call_events.sql` (if new columns needed)
- `demo/dms-ui/` — export action button (optional for gate; required for demo story)
- `tests/dms/test_tool_call.py`, `tests/execution/test_tool_call_node.py`

## Anti-scope — do NOT
- Production Firecracker / full WASM toolchain (P2 parking lot)
- Arbitrary code execution or open filesystem/network from sandbox
- New parallel audit store — use F1 ledger only
- Skip F5 gate or run actions on `requires_confirm=False` brain outputs without explicit policy
- Real customer data before F7 remainder (RBAC + RLS + rate limit) ships

## Sequencing
```
Gate F5 PASS → F6 skill capture → F7 remainder (RBAC/RLS/rate limit) → F8 tool-call execution
```
Vertical slice exception: single `export_pptx` behind steward role + ledger, for internal demo only.

## Next after PASS
Wire additional tools from F6 skill library (`required_tools` per card). Consider Temporal durable DAG (P3) only when run volume warrants it.
