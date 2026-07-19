# TOUCH_MAP — where AI coding agents may and may not touch

Strict guidance for every agent (Claude, Cursor, subagents) working in this
repo. The goal: an agent must know, before editing, whether a file is safe,
needs sign-off, or is off-limits. Pair this with `AGENTS.md` roles and the
gate discipline in `STATUS.md`.

## NEVER TOUCH (hard no — regardless of task framing)

| Path | Why |
|---|---|
| `packs/dms/audit/ledger.py` — hash/verify logic | Tamper-evident chain; any edit invalidates trust claims. Additive columns only via explicit gate. |
| `packs/dms/security/` (prompt_harness, scam_guard, pii, api_auth) | Bank-grade gate semantics; changes need adversarial re-test (`data/security/adversarial_prompts.jsonl`). |
| `CortexOS/dms/sql_guardrail.py` deny rules | Loosening = data exfil path. Widening allowlists requires owner sign-off. |
| `*.db`, `*.duckdb`, `*.sqlite` binaries in git | Never hand-edit or regenerate silently; they carry ledger state. |
| `env.local`, `key.md`, any secrets | Read env var *names* only; never print/copy values, never commit. |
| `myenv/`, `node_modules/`, `third_party/` | Vendored; regenerate, don't edit. |
| Actor-from-key rule (F7): API `actor` always derives from the authenticated key | Restated in STATUS.md; no route may trust client-supplied actor/role. |

## ASK FIRST (propose, get owner/gate sign-off, then edit)

| Path | Why |
|---|---|
| `packs/dms/semantic_layer.yaml` | Contract for NL→SQL + guardrail column allowlist; UI and tests depend on it. |
| `packs/dms/rules/` compliance rules, `packs/dms/tasks/gate.py` | F5 gate semantics. |
| `pyproject.toml` deps / extras | Portable-SSD + fresh-clone demo paths break easily. |
| `STATUS.md`, `PARKING_LOT.md`, gate docs (`docs/dms/*GATE*`) | Owner's process spine; agents append, never rewrite history. |
| Public route shapes in `CortexOS/api/*` (incl. `sidecar_routes.py`) | AirGPT's `cortex_client.py` and demo UI pin these response shapes. |
| `.github/workflows/` CI | Green-on-push is a demo asset. |

## FREE TO TOUCH (normal engineering judgement)

- `demo/dms-ui/` — demo frontend (keep `lib/api.js` response-shape assumptions in sync with the API).
- `docs/` — additive docs always welcome.
- `C:\Users\user\Playground\Apps\DMS` — standalone DMS v3 app (out-of-repo host shell).
- New packs under `packs/` (scaffolds), new tests under `tests/`.
- `scripts/`, `demo/run_demo.ps1` conveniences — keep fresh-clone path working.

## Rules of engagement

1. **Additive over destructive:** prefer new module + registration over editing a
   frozen one.
2. **Every schema/route change lands with its test** in the same change set.
3. **Binary state lives outside the repo** (Playground\Database pattern) — new
   features must take a DB-path env override like `DMS_OPS_DB` /
   `DMS_WAREHOUSE_DB`.
4. **When in doubt, it's ASK FIRST.** The cost asymmetry is extreme: a wrong
   edit in NEVER TOUCH silently breaks trust guarantees that demos sell.
