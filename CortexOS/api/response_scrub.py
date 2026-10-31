"""Response-boundary scrub of names the caller's grant does not cover (NAME-ECHO-CX-01).

A model can write SQL that names tables or columns it invented, or that exist
but are outside the session's grant. Refusal and retry text quotes those names
("UNKNOWN_TABLE:<name>", a DuckDB binder error, the model's own reconfirm
prose), and without this module they reached the caller in the answer prose,
assumptions, ``served_reason``, the reconfirm fields and HTTP error details.

At each user-facing exit of ``/v1/contract/ask`` and
``/v1/contract/drillthrough`` the route builds a :class:`NameScrub` and applies
it to free-text fields only. ``POST /v1/insights`` and the MCP tool
``answer_engine.answer`` return plain dict envelopes and use
:func:`scrub_envelope`, the same scrub over named free-text keys
(NAME-ECHO-INSIGHTS-01). The names it withholds are:

* every identifier the request's SQL attempts referenced (each statement the
  SQL gate or the executor saw, recorded by :mod:`CortexOS.sql_attempts` and
  parsed with sqlglot): table, schema and catalog parts at any depth,
  columns and struct fields, ``USING`` columns, and the identifier tokens of
  anything sqlglot could only keep as an opaque command. Labels the statement
  defines (aliases, CTE and window names, lambda parameters, struct keys,
  named arguments) are not references;
* every function and type name sqlglot does not know, in call or relation
  position (``FROM f()``, ``JOIN f()``, ``LATERAL f()``, ``s.f(x)``,
  ``CAST(x AS t)``), unless the engine defines it as a built-in; and
* every double-quoted identifier in the error text those attempts produced,
  and the object DuckDB's catalog error names unquoted
  (``... with name <name> does not exist``),

minus the names the grant covers: the granted tables, their schema parts
(``main`` for a bare key) and the columns of those tables in the warehouse
catalog. A bare key covers only ``main.<table>`` of the default catalog, as
the manifest grants it; a same-named table in another schema is not granted
and its columns are withheld. Names of any length are withheld. Names are
compared with :func:`CortexOS.identifier_fold.fold_identifier`, the rule
DuckDB binds by. The caller's own words are never withheld: names in the
question, the session and Space ids, and the tables and columns of the DMS
payload are kept, as are the column names of the rows being returned.

Never changed: ``rows``, ``sql_used``, ``contributing_sources``, the drill
response (it has no free text), ids, codes and every other structured field.
On an answer that serves SQL, ``answer`` is text built from the rows and is
left as it is. ``reconfirm.closest_question`` is never returned scrubbed:
Yes resends it as the question, so when the scrub would change it, Cortex's
own closest question from the grant replaces it, and a reconfirm answer is
rendered again from its scrubbed ``why`` and that question. Each statement of
an offered reading (contract 1.10.0, ``reconfirm.readings``) is scrubbed as
free text and capped again to the published length, and the reconfirm answer
is rendered from those statements; ``reading_id`` and ``row_count`` are kept.
Decisions come from parsed SQL, error text and the grant; no
rule reads the question for words. No pack import and no database driver
here: the catalog reads go through :mod:`CortexOS.execution.submit`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.execution.sql_scope import ScopeError, cte_reference_ids
from CortexOS.identifier_fold import fold_identifier
from CortexOS.sql_attempts import AttemptRecord

#: What a withheld name is replaced with.
WITHHELD = "[name withheld]"

_DIALECT = "duckdb"
_DEFAULT_SCHEMA = "main"
# A double-quoted identifier in engine error text ("" is an escaped quote).
_QUOTED_IDENT = re.compile(r'"((?:[^"]|"")+)"')
_WORD = re.compile(r"\w+")
# DuckDB's catalog error names the missing object unquoted:
# "Scalar Function with name f does not exist!".
_CATALOG_MISSING = re.compile(r"\bwith name ([^\s\"'!?,;()]+) does not exist")
# Terminal highlighting (sqlglot underlines the failing token in a parse error).
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Clauses of a query whose bare names may refer to that query's output columns.
_OUTPUT_CLAUSES = frozenset({"order", "group", "having", "qualify"})


# -- names an attempt referenced ---------------------------------------------------


def _identifier(node: object) -> str | None:
    if isinstance(node, exp.Identifier) and node.name.strip():
        return node.name
    return None


def _labels(tree: exp.Expression) -> set[str]:
    """Folded alias and CTE names the statement itself defines."""
    labels: set[str] = set()
    for alias in tree.find_all(exp.TableAlias):
        name = _identifier(alias.this)
        if name:
            labels.add(fold_identifier(name))
        for column in alias.columns:
            col = _identifier(column)
            if col:
                labels.add(fold_identifier(col))
    return labels


def _refers_to_output(column: exp.Column) -> bool:
    """True for a bare name in ORDER BY / GROUP BY / HAVING / QUALIFY that names an output column.

    Such a name reads the query's own output (``ORDER BY n`` after ``COUNT(*) AS n``);
    the reference that names data is the one in the select list.
    """
    if column.table:
        return False
    child: exp.Expression = column
    parent = column.parent
    while parent is not None and not isinstance(parent, exp.Query):
        child, parent = parent, parent.parent
    if parent is None or child.arg_key not in _OUTPUT_CLAUSES:
        return False
    try:
        outputs = {fold_identifier(name) for name in parent.named_selects}
    except Exception:  # noqa: BLE001 - unknown shape: count it as a reference
        return False
    return fold_identifier(column.name) in outputs


def _defined_identifier_ids(tree: exp.Expression, cte_refs: set[int]) -> set[int]:
    """Identifier nodes that define a name, or read one the statement defined.

    Alias, CTE and window labels, lambda parameters, struct keys and named
    arguments, the output columns sqlglot synthesises for a PIVOT, a table
    reference scope binds to a CTE, and a bare ORDER BY / GROUP BY / HAVING /
    QUALIFY name that reads an output column. Every other identifier in the
    statement is a reference.
    """
    ids: set[int] = set()

    def mark(node: object) -> None:
        if isinstance(node, exp.Identifier):
            ids.add(id(node))

    for alias in tree.find_all(exp.TableAlias):
        mark(alias.this)
        for column in alias.columns:
            mark(column)
    for named in tree.find_all(exp.Alias):
        mark(named.args.get("alias"))
    for window in tree.find_all(exp.Window):
        mark(window.this)
        mark(window.args.get("alias"))
    for keyed in tree.find_all(exp.PropertyEQ, exp.Kwarg):
        mark(keyed.this)
    for pivot in tree.find_all(exp.Pivot):
        for column in pivot.args.get("columns") or []:
            mark(column)
    for lam in tree.find_all(exp.Lambda):
        params = {fold_identifier(p.name) for p in lam.expressions if isinstance(p, exp.Identifier)}
        for param in lam.expressions:
            mark(param)
        for ident in lam.this.find_all(exp.Identifier):
            if fold_identifier(ident.name) in params:
                mark(ident)
    for table in tree.find_all(exp.Table):
        if id(table) in cte_refs:
            for key in ("this", "db", "catalog"):
                mark(table.args.get(key))
    labels = _labels(tree)
    for column in tree.find_all(exp.Column):
        if _refers_to_output(column):
            mark(column.this)
        for key in ("table", "db", "catalog"):
            part = column.args.get(key)
            if isinstance(part, exp.Identifier) and fold_identifier(part.name) in labels:
                mark(part)
    return ids


def _command_text(command: exp.Command) -> str:
    rest = command.args.get("expression")
    if isinstance(rest, exp.Literal):
        return str(rest.this)
    return str(rest or "")


def _tree_names(tree: exp.Expression) -> tuple[set[str], set[str]]:
    """``(identifiers, engine_names)`` a statement references, as written.

    ``identifiers`` holds every identifier in the tree that is not a label the
    statement defines (:func:`_defined_identifier_ids`): table, schema and
    catalog parts at any depth (``a.b.c.d`` puts its last parts in a ``Dot``),
    columns, struct fields, ``USING`` columns, the schema of a qualified call,
    and the identifier tokens of any part sqlglot kept as an opaque
    ``Command``. ``engine_names`` holds names in the engine's own namespace:
    functions sqlglot does not know, in call or relation position
    (``FROM f()``, ``JOIN f()``, ``LATERAL f()``, ``s.f(x)``), and types it
    does not know (``CAST(x AS t)``). Those are withheld unless they are
    engine built-ins (see :func:`build_scrub`).
    """
    try:
        cte_refs = cte_reference_ids(tree, allow_recursive=True)
    except ScopeError:
        cte_refs = set()
    defined = _defined_identifier_ids(tree, cte_refs)
    identifiers = {
        ident.name
        for ident in tree.find_all(exp.Identifier)
        if id(ident) not in defined and ident.name.strip()
    }
    for command in tree.find_all(exp.Command):
        identifiers |= _token_identifiers(_command_text(command))
    engine_names: set[str] = set()
    for call in tree.find_all(exp.Anonymous):
        if isinstance(call.this, str) and call.this.strip():
            engine_names.add(call.this)
    for dtype in tree.find_all(exp.DataType):
        if dtype.this == exp.DataType.Type.USERDEFINED:
            kind = dtype.args.get("kind")
            name = kind.name if isinstance(kind, exp.Expression) else str(kind or "")
            if name.strip():
                engine_names.add(name)
    return identifiers, engine_names


def _token_identifiers(sql: str) -> set[str]:
    """Identifier tokens of SQL sqlglot cannot parse (its parse errors quote the text)."""
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.tokens import TokenType

    try:
        tokens = Dialect.get_or_raise(_DIALECT).tokenize(sql)
    except Exception:  # noqa: BLE001 - untokenisable text names nothing we can place
        return set()
    return {
        token.text
        for token in tokens
        if token.token_type in (TokenType.VAR, TokenType.IDENTIFIER) and token.text.strip()
    }


def sql_names(sql: str) -> tuple[set[str], set[str]]:
    """``(identifiers, engine_names)`` ``sql`` references, as written (see :func:`_tree_names`)."""
    try:
        trees = [tree for tree in sqlglot.parse(sql, read=_DIALECT) if tree is not None]
    except Exception:  # noqa: BLE001 - unparseable: fall back to its identifier tokens
        return _token_identifiers(sql), set()
    identifiers: set[str] = set()
    engine_names: set[str] = set()
    for tree in trees:
        found, called = _tree_names(tree)
        identifiers |= found
        engine_names |= called
    return identifiers, engine_names


def sql_identifiers(sql: str) -> set[str]:
    """Every relation, column, function and type name ``sql`` references, as written."""
    identifiers, engine_names = sql_names(sql)
    return identifiers | engine_names


def error_identifiers(text: str) -> set[str]:
    """Identifiers engine error text names, split at dots.

    Double-quoted identifiers, and the object of DuckDB's catalog error
    (``<Kind> with name <name> does not exist``), which it writes unquoted.
    """
    names: set[str] = set()
    raws = [m.group(1).replace('""', '"') for m in _QUOTED_IDENT.finditer(text or "")]
    raws += [m.group(1) for m in _CATALOG_MISSING.finditer(text or "")]
    for raw in raws:
        for part in raw.split("."):
            part = part.strip()
            if part and _WORD.fullmatch(part) and not part.isdigit():
                names.add(part)
    return names


# -- the scrub -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NameScrub:
    """Replaces each withheld name (matched after folding, as a whole word) in a text."""

    withheld: frozenset[str]

    def _pattern(self) -> re.Pattern[str] | None:
        if not self.withheld:
            return None
        alts = "|".join(re.escape(name) for name in sorted(self.withheld, key=len, reverse=True))
        return re.compile(rf"(?<!\w)(?:{alts})(?!\w)")

    def text(self, value: str) -> str:
        pattern = self._pattern()
        if pattern is None or not value:
            return value
        # A highlight code ends in a letter ("\x1b[4m"), which would hide the
        # word edge in front of the name it highlights.
        value = _ANSI.sub("", value)
        # fold_identifier maps one character to one character, so spans found in
        # the folded text are spans of the original.
        out: list[str] = []
        last = 0
        for match in pattern.finditer(fold_identifier(value)):
            out.append(value[last : match.start()])
            out.append(WITHHELD)
            last = match.end()
        if not out:
            return value
        out.append(value[last:])
        return "".join(out)

    def optional(self, value: str | None) -> str | None:
        return None if value is None else self.text(value)


def _in_text(folded_name: str, folded_texts: list[str]) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(folded_name)}(?!\w)")
    return any(pattern.search(text) for text in folded_texts)


def _grant_names(verified: Any) -> set[str]:
    """The granted tables and their schema parts.

    A bare key grants the table in DuckDB's default schema (``main.t`` is ``t``,
    as the manifest matches it), so ``main`` is kept with it.
    """
    names: set[str] = set()
    for key in getattr(verified, "row_predicates", {}) or {}:
        parts = [part for part in str(key).split(".") if part]
        if len(parts) == 1:
            names.add(_DEFAULT_SCHEMA)
        names.update(fold_identifier(part) for part in parts)
    return names


def build_scrub(
    attempts: AttemptRecord,
    *,
    verified: Any,
    caller_texts: Iterable[str] = (),
    caller_names: Iterable[str] = (),
    errors: Iterable[str] = (),
) -> NameScrub:
    """The names to withhold for one request.

    ``verified`` is the session grant (None when the request never resolved
    one: then no name is granted). ``caller_texts`` are the request's own
    strings (question, ids): a name that appears in one as a whole word is
    kept. ``caller_names`` are names the caller declared or that the response
    already carries as structure (payload tables and columns, row keys).
    ``errors`` is error text produced at the exit itself.
    """
    candidates: set[str] = set()
    called: set[str] = set()
    for sql in attempts.sql:
        identifiers, engine_names = sql_names(sql)
        candidates |= {fold_identifier(name) for name in identifiers}
        called |= {fold_identifier(name) for name in engine_names}
    for text in [*attempts.errors, *errors]:
        candidates |= {fold_identifier(name) for name in error_identifiers(text)}
    # A name only ever written in call or type position may be the engine's own.
    called -= candidates
    candidates |= called
    # No length cap: a long name is withheld like a short one. The record bounds
    # how much SQL and error text there is (sql_attempts.MAX_ENTRIES/MAX_CHARS).
    candidates = {name for name in candidates if name.strip()}
    if not candidates:
        return NameScrub(frozenset())

    kept = _grant_names(verified) | {fold_identifier(str(n)) for n in caller_names if n}
    remaining = candidates - kept
    folded_texts = [fold_identifier(str(text)) for text in caller_texts if text]
    remaining = {name for name in remaining if not _in_text(name, folded_texts)}
    if remaining and verified is not None:
        from CortexOS.execution.submit import granted_column_names

        remaining -= granted_column_names(verified)
    if remaining & called:
        from CortexOS.execution.submit import engine_builtin_names

        remaining -= called & engine_builtin_names()
    return NameScrub(frozenset(remaining))


# -- exits --------------------------------------------------------------------------


def ask_caller_context(body: Any) -> tuple[list[str], set[str]]:
    """``(caller_texts, caller_names)`` of a contract ask request."""
    texts = [
        str(value)
        for value in (
            getattr(body, "question", None),
            getattr(body, "session_id", None),
            getattr(body, "space_id", None),
        )
        if value
    ]
    names: set[str] = set()
    payload = getattr(body, "dms_payload", None)
    if payload is not None:
        for table in payload.tables:
            names.add(table.name)
            names.update(column.name for column in table.columns)
        for join in payload.joins:
            names.update((join.left_table, join.left_column, join.right_table, join.right_column))
    return texts, names


def _row_keys(rows: Iterable[Mapping[str, Any]] | None) -> set[str]:
    keys: set[str] = set()
    for row in rows or []:
        if isinstance(row, Mapping):
            keys.update(str(key) for key in row)
    return keys


def _closest_question(scrub: NameScrub, closest: str, fallback: Callable[[], str] | None) -> str:
    """A closest question with no withheld marker in it.

    Yes resends ``closest_question`` as the question, so a scrubbed one would
    run broken text. When the model's question names a withheld word, Cortex's
    own question from the grant (``fallback``) replaces it whole.
    """
    scrubbed = scrub.text(closest)
    if scrubbed == closest or fallback is None:
        return scrubbed
    replacement = fallback()
    if replacement and scrub.text(replacement) == replacement:
        return replacement
    return scrubbed


def scrub_answer(
    answer: Any,
    attempts: AttemptRecord,
    *,
    verified: Any,
    caller_texts: Iterable[str] = (),
    caller_names: Iterable[str] = (),
    closest_fallback: Callable[[], str] | None = None,
    reconfirm_text: Callable[..., str] | None = None,
) -> Any:
    """``answer`` (a contract ``Answer``) with withheld names replaced in its free text.

    ``closest_fallback`` builds Cortex's own closest question from the grant;
    it replaces a model closest question the scrub would change.
    ``reconfirm_text(why, closest)`` renders a reconfirm answer: when the answer
    is that rendering, it is rendered again from the scrubbed ``why`` and the
    final closest question, so Cortex's own wording is never scrubbed. With
    offered readings it is called as ``reconfirm_text(why, closest,
    readings=[(statement, row_count), ...])``; without them, with two
    arguments as before.
    """
    scrub = build_scrub(
        attempts,
        verified=verified,
        caller_texts=caller_texts,
        caller_names={*caller_names, *_row_keys(answer.rows)},
    )
    if not scrub.withheld:
        return answer
    update: dict[str, Any] = {
        "assumptions": [scrub.text(line) for line in answer.assumptions],
        "suggestions": [scrub.text(line) for line in answer.suggestions],
        "served_reason": scrub.optional(answer.served_reason),
    }
    # A served answer's text is rendered from its rows; it is not touched.
    if not (answer.sql_used or answer.rows):
        update["answer"] = scrub.text(answer.answer)
    if answer.provenance.assumptions is not None:
        update["provenance"] = answer.provenance.model_copy(
            update={"assumptions": scrub.text(answer.provenance.assumptions)}
        )
    if answer.reconfirm is not None:
        why = scrub.text(answer.reconfirm.why)
        closest = _closest_question(scrub, answer.reconfirm.closest_question, closest_fallback)
        offered = list(getattr(answer.reconfirm, "readings", None) or [])
        if not offered:
            update["reconfirm"] = answer.reconfirm.model_copy(
                update={"why": why, "closest_question": closest}
            )
            rendered = reconfirm_text is not None and answer.answer == reconfirm_text(
                answer.reconfirm.why, answer.reconfirm.closest_question
            )
            if rendered and reconfirm_text is not None:
                update["answer"] = reconfirm_text(why, closest)
            return answer.model_copy(update=update)
        # A withheld marker is longer than many names, so a scrubbed statement
        # is capped again: model_copy does not validate, and the published
        # maxLength must hold on the wire.
        from CortexOS.plan_sql.describe import cap_statement

        scrubbed = [
            item.model_copy(update={"statement": cap_statement(scrub.text(item.statement))})
            for item in offered
        ]
        update["reconfirm"] = answer.reconfirm.model_copy(
            update={"why": why, "closest_question": closest, "readings": scrubbed}
        )
        rendered = reconfirm_text is not None and answer.answer == reconfirm_text(
            answer.reconfirm.why,
            answer.reconfirm.closest_question,
            readings=[(item.statement, item.row_count) for item in offered],
        )
        if rendered and reconfirm_text is not None:
            update["answer"] = reconfirm_text(
                why,
                closest,
                readings=[(item.statement, item.row_count) for item in scrubbed],
            )
    return answer.model_copy(update=update)


def scrub_http_detail(
    detail: Any,
    attempts: AttemptRecord,
    *,
    verified: Any,
    caller_texts: Iterable[str] = (),
    caller_names: Iterable[str] = (),
) -> Any:
    """An HTTP error ``detail`` with withheld names replaced. ``code`` is kept as is."""
    if isinstance(detail, Mapping):
        texts = [
            str(value) for key, value in detail.items() if key != "code" and isinstance(value, str)
        ]
    elif isinstance(detail, str):
        texts = [detail]
    else:
        return detail
    scrub = build_scrub(
        attempts,
        verified=verified,
        caller_texts=caller_texts,
        caller_names=caller_names,
        errors=texts,
    )
    if not scrub.withheld:
        return detail
    if isinstance(detail, str):
        return scrub.text(detail)
    return {
        key: scrub.text(value) if key != "code" and isinstance(value, str) else value
        for key, value in detail.items()
    }


#: Keys of a JSON envelope whose string values (or lists of strings) are free
#: text: prose, refusal and retry reasons, notes. Only these are scrubbed.
FREE_TEXT_KEYS = frozenset(
    {
        "answer",
        "assumptions",
        "detail",
        "error",
        "message",
        "note",
        "notes",
        "reason",
        "refuse_reason",
        "served_reason",
        "suggestions",
        "warning",
        "why",
    }
)
#: Keys whose whole value is left as it is: rows, values and SQL the caller
#: reads as data, the caller's own words, the ranking Cortex served the model
#: from, and Cortex's own constant blocks.
KEPT_KEYS = frozenset(
    {
        "api",
        "export_runtime",
        "extracted_sql",
        "intent",
        "law",
        "ontology",
        "query_sql",
        "question",
        "rows",
        "schema_context",
        "sql",
        "sql_used",
        "usage",
        "values",
    }
)


def _scrub_tree(node: Any, scrub: NameScrub, *, free: bool) -> Any:
    if isinstance(node, Mapping):
        return {
            key: value
            if key in KEPT_KEYS
            else _scrub_tree(value, scrub, free=key in FREE_TEXT_KEYS)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_scrub_tree(item, scrub, free=free) for item in node]
    if free and isinstance(node, str):
        return scrub.text(node)
    return node


def split_names(names: Iterable[str]) -> set[str]:
    """Each name and its dot-separated parts (``schema.table`` keeps both parts)."""
    out: set[str] = set()
    for name in names:
        text = str(name or "").strip()
        if not text:
            continue
        out.add(text)
        out.update(part for part in text.split(".") if part.strip())
    return out


def scrub_envelope(
    envelope: Mapping[str, Any],
    attempts: AttemptRecord,
    *,
    verified: Any,
    caller_texts: Iterable[str] = (),
    caller_names: Iterable[str] = (),
) -> Any:
    """A JSON answer envelope with withheld names replaced in its free text.

    For exits that return a plain dict instead of a contract ``Answer``
    (``POST /v1/insights``, the MCP ``answer_engine.answer`` tool). Only string
    values under :data:`FREE_TEXT_KEYS` change, at any depth; nothing under
    :data:`KEPT_KEYS` is read or changed, so rows, values and SQL stay
    byte-for-byte. A served envelope (rows or values present, or status
    ``CERTIFIED``) keeps its top-level ``answer``: that text is rendered from
    the rows. SQL alone does not make an envelope served: insights copies a
    generated statement into ``sql_used`` before it runs, so an abstain can
    carry SQL, no rows, and error text quoting the names it invented. With
    nothing to withhold the envelope itself is returned.
    """
    rows = envelope.get("rows")
    scrub = build_scrub(
        attempts,
        verified=verified,
        caller_texts=caller_texts,
        caller_names={*caller_names, *_row_keys(rows if isinstance(rows, list) else None)},
    )
    if not scrub.withheld:
        return envelope
    served = bool(rows or envelope.get("values")) or envelope.get("status") == "CERTIFIED"
    out: dict[str, Any] = {}
    for key, value in envelope.items():
        if key in KEPT_KEYS or (served and key == "answer"):
            out[key] = value
        else:
            out[key] = _scrub_tree(value, scrub, free=key in FREE_TEXT_KEYS)
    return out


__all__ = [
    "FREE_TEXT_KEYS",
    "KEPT_KEYS",
    "WITHHELD",
    "NameScrub",
    "ask_caller_context",
    "build_scrub",
    "error_identifiers",
    "scrub_answer",
    "scrub_envelope",
    "scrub_http_detail",
    "split_names",
    "sql_identifiers",
    "sql_names",
]
