# JOIN-GRANT-ZW: an invisible character hides a table from every SQL gate (2026-11-20)

Found by the #408 adversary round 3 (Opus, identifier lens). P0, gate bypass.
Fixed on `claude/track-cortex-accuracy`.

## Expected vs actual (main `304bf84` and #408 head `d4a62b6`)

- Expected: SQL that reads a table outside the grant is refused by the join
  grant, the caller-catalog gate and the manifest enforcer.
- Actual: `SELECT * FROM orders AS o<U+200B>CROSS<U+200B>JOIN<U+200B>users`
  passed all three. sqlglot keeps U+200B (also U+2060, U+FEFF) inside the alias
  and writes it back unquoted; DuckDB reads it as whitespace. Every gate saw
  one table; DuckDB cross-joined `users` with no row predicate. Against a
  manifest with `orders: tenant_id = 'a'`, DuckDB returned the other tenant's
  `users` row. A `UNION ALL SELECT col FROM t` hidden the same way returns that
  table's values. It also reads undeclared tables through `/v1/insights`.

## Repro

`python -m pytest tests/test_execution/test_sql_codepoints_zw.py -q` with the
three call sites reverted: 36 failed. Probe on main code:
`probe.py pair_no_join "SELECT * FROM alpha_ledger a​CROSS​JOIN​beta_entry WHERE qty > 1"`
gives `result: BYPASS`, DuckDB reads both tables.

## Root-cause class

**Tokenizer differential.** A gate that decides on one parser's tree while a
different parser executes the text is only as safe as their agreement on token
boundaries. A full sweep of all 1,112,064 code points (`a<ch>b` through
sqlglot's tokenizer and DuckDB's `json_serialize_sql`) found 27 that sqlglot
keeps in one identifier and DuckDB splits or truncates: 24 C0 controls and
DEL (DuckDB truncates the statement at NUL), U+200B, U+2060, U+FEFF. The
round-1 finding listed zero-width characters as "refused by the parser" with
the unicode sweep unfinished. That unfinished sweep was the gap.

## Fix

`CortexOS/sql_codepoints.py` refuses, before any parse and anywhere in the
SQL: control characters other than tab, LF and CR; lone surrogates; and the
three format characters DuckDB reads as whitespace (U+200B, U+2060, U+FEFF).
Called first in all five SQL gates:

- `execution.manifest._parse_single_statement` (`SqlNotAnalyzable`);
- `insights.caller_sql.validate_caller_sql`;
- `insights.schema_context.ungranted_reason` (`unanalysable_join`; the route
  serves the caller gate's named reason);
- `dms.sql_guardrail.validate_sql` (`HIDDEN_CODEPOINT:U+XXXX`). The verifier
  round found this one still open: `POST /dms/lakehouse/query` runs the
  caller's SQL behind it with no manifest, and
  `i<ZW>CROSS<ZW>JOIN<ZW>read_csv('<file>')` returned the file;
- the insights pack path, `crew.cot_climb._check_sql`, ahead of the frozen
  `crew.freeroute.validate_sql` (that file belongs to the #215 lane and
  `test_branch_does_not_dual_write_freeroute_layer` holds it; a first version
  edited it and that guard went red).

The first version refused every Cf, Zl, Zp, Co and Cn code point. The verifier
showed that refuses real data values both parsers read the same way (ZWJ emoji,
Persian ZWNJ, soft hyphen, bidi marks, emoji newer than Python's Unicode table),
so the rule was narrowed to what is measured to split plus controls. The CI
test sweeps every allowed code point (about 1.11M) through both tokenizers in
12 s; dropping U+2060 from the set fails it by name.

Sweep record, corrected: commit 8a3a0e7 says the verifier's 16-position sweep
covered 10,145 newly allowed code points. That run was the writer's, with the
verifier's script, over the 162 newly allowed Cf/Zl/Zp code points plus a
1-in-97 sample of Cn/Co (10,145 in all): 0 differentials. The verifier then ran
its own sweep over every Cf, Zl, Zp and Co code point plus 40,000 Cn (177,630
code points, 16 positions, about 2.84M probes): 0, with U+200B, U+2060, U+FEFF
still found as a self-check. Verdict at 8a3a0e7: HOLDS.

## Part 2: a comment changed a literal (WRONG=0)

DuckDB strips Unicode spaces outside quotes before parsing, and its quote
tracking does not skip `/* */` comments. sqlglot renders every comment as
`/* */`, so `-- customer's filter` flips the pass and an NBSP inside a later
literal becomes a plain space: `WHERE v = 'a<NBSP>b'` returned the `'a b'` row.
Only U+00A0, U+2000-U+200A, U+202F, U+205F and U+3000 are rewritten (verifier
sweep of all allowed code points in four contexts). E-strings and dollar quotes
also flip the pass in raw SQL, but sqlglot re-renders them as plain strings, so
in served SQL a comment is the only flipper. The gates now render served SQL
with `comments=False` (manifest `_served`, caller gate, guardrail and its two
page helpers). Comments kept in all four: 12 tests red; in the manifest alone: 4.
Five must-pass statements with comments, `--` and `/*` inside literals return
the same rows before and after.

## Invariants that applied

- Adversary is not the verifier: the writer's 53 cases and two Sonnet rounds
  passed; an Opus attacker found it in one lens.
- Fix the root-cause class: every SQL gate refuses before parsing, and the
  sweep proves no allowed code point splits differently, so the rule is
  measured, not guessed. Dropping one character from the set fails it by name.
- Prove a gate can fail: each call site removed alone goes red (manifest 8,
  caller gate 9, join grant 17, guardrail 8, crew check 1); the first three
  removed together, 36.
- A control that blocks legitimate work is itself a failure: the first rule
  refused ZWJ emoji in literals; narrowed, with must-pass cases for each.

## Open, by name

- GUARDRAIL-STAR-REWRITE-INVENTORY (verifier, pre-existing, for routing): with
  sensitive columns declared, `validate_sql` rewrites any `SELECT *` to the
  inventory table's columns, whatever table is read, so a star query on
  `POST /dms/lakehouse/query` can return 500. The server log names the bound
  relation; the body does not.
- Other sqlglot parse sites (`sql_lineage`, `plan_sql/describe`,
  `verified_query_ask`, `l2_plan_gates`, `drillthrough`) are decision helpers
  ahead of `execute_sql`, which runs `enforce_manifest`. Not audited one by one.
- `CortexOS/plan_sql/payload.py` strips Cf from sampled values for prompts; it
  is a prompt control, not a SQL gate, and was not changed.
