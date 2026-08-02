# Ponytail — token reduction for Cortex builds

[Ponytail](https://github.com/DietrichGebert/ponytail) enforces lazy-senior-dev mode: delete before add, stdlib first, no speculative abstractions.

## Install (Claude Code / Cursor plugin)

```text
/plugin marketplace add DietrichGebert/ponytail
/plugin install ponytail@ponytail
```

## Modes

| Mode | Use when |
|---|---|
| `lite` | Quick fixes, docs-only |
| `full` | **Default** — feature ships, DMS builds |
| `ultra` | Debt paydown, delete dead code |
| `off` | Gate verification read-only |

## Config

```powershell
$env:PONYTAIL_DEFAULT_MODE = "full"
```

Or `~/.config/ponytail/config.json`:

```json
{"defaultMode": "full"}
```

## Cortex workflow

1. **Before coding:** read `STATUS.md` (the one state file, capped at 60 lines)
2. **During build:** prefer extending existing functions over new modules
3. **Before merge:** ponytail-review mindset — remove unused imports, collapse one-liner helpers
4. **Never ponytail:** security harness, ledger tests, adversarial corpus

## Token savings targets

- Handoff files: link to STATUS.md instead of duplicating tables
- Subagent prompts: one feature + acceptance tests only
- Middleware: prefetch semantic layer once per request (see `query_service.py`)

Benchmark reference: ~16% fewer tokens on repetitive coding tasks (upstream Ponytail README).
