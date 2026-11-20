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

`CortexOS/sql_codepoints.py` refuses a class before any parse: Cc (except tab,
LF, CR), Cf, Zl, Zp, Cs, Co and Cn, anywhere in the SQL. Called first in
`execution.manifest._parse_single_statement` (`SqlNotAnalyzable`),
`insights.caller_sql.validate_caller_sql` and
`insights.schema_context.ungranted_reason` (`unanalysable_join`; the route
serves the caller gate's named reason). The test sweeps every allowed code
point through both tokenizers, so an upgrade that adds a disagreement fails CI.

## Invariants that applied

- Adversary is not the verifier: the writer's 53 cases and two Sonnet rounds
  passed; an Opus attacker found it in one lens.
- Fix the root-cause class: the rule refuses the categories, and the sweep
  proves no allowed code point splits differently. Narrowing the rule to Cf
  only leaves 7 tests red (the controls).
- Prove a gate can fail: each of the three call sites removed alone goes red
  (8, 9, 17 failures); all three removed, 36.

## Open, by name

- The sweep covers the `a<ch>b` identifier position. A character that both
  tokenizers keep but in different token kinds elsewhere (inside a number,
  after a quote) was not swept. The served SQL is sqlglot's re-rendering, which
  re-quotes strings and comments, so those positions are normalised before
  DuckDB sees them.
- `CortexOS/plan_sql/payload.py` strips Cf from sampled values for prompts; it
  is a prompt control, not a SQL gate, and was not changed.
