"""Response-boundary scrub of names the caller's grant does not cover (NAME-ECHO-CX-01).

A model can write SQL that names tables or columns it invented, or that exist
but are outside the session's grant. Refusal and retry text quotes those names
("UNKNOWN_TABLE:<name>", a DuckDB binder error, the model's own reconfirm
prose), and without this module they reached the caller in the answer prose,
assumptions, ``served_reason``, the reconfirm fields and HTTP error details.

At each user-facing exit of ``/v1/contract/ask`` and
``/v1/contract/drillthrough`` the route builds a :class:`NameScrub` and applies
it to free-text fields only. The names it withholds are:

* every table, schema and column identifier the request's SQL attempts
  referenced (each statement the SQL gate or the executor saw, recorded by
  :mod:`CortexOS.sql_attempts` and parsed with sqlglot; alias and CTE labels
  the statement defines are not references), and
* every double-quoted identifier in the error text those attempts produced,

minus the names the grant covers: the granted tables (and their schema
parts) and the columns of those tables in the warehouse catalog. Names are
compared with :func:`CortexOS.identifier_fold.fold_identifier`, the rule
DuckDB binds by. The caller's own words are never withheld: names in the
question, the session and Space ids, and the tables and columns of the DMS
payload are kept, as are the column names of the rows being returned.

Never changed: ``rows``, ``sql_used``, ``contributing_sources``, the drill
response (it has no free text), ids, codes and every other structured field.
On an answer that serves SQL, ``answer`` is text built from the rows and is
left as it is. Decisions come from parsed SQL, error text and the grant; no
rule reads the question for words. No pack import and no database driver
here: the catalog read goes through :mod:`CortexOS.execution.submit`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
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
_MAX_NAME = 128
# A double-quoted identifier in engine error text ("" is an escaped quote).
_QUOTED_IDENT = re.compile(r'"((?:[^"]|"")+)"')
_WORD = re.compile(r"\w+")
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


def _tree_identifiers(tree: exp.Expression) -> set[str]:
    try:
        cte_refs = cte_reference_ids(tree, allow_recursive=True)
    except ScopeError:
        cte_refs = set()
    labels = _labels(tree)
    names: set[str] = set()
    for table in tree.find_all(exp.Table):
        if id(table) in cte_refs:
            continue
        for key in ("this", "db", "catalog"):
            name = _identifier(table.args.get(key))
            if name:
                names.add(name)
    for column in tree.find_all(exp.Column):
        name = _identifier(column.this)
        if name and not _refers_to_output(column):
            names.add(name)
        for key in ("table", "db", "catalog"):
            part = _identifier(column.args.get(key))
            if part and fold_identifier(part) not in labels:
                names.add(part)
    return names


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


def sql_identifiers(sql: str) -> set[str]:
    """Table, schema and column names ``sql`` references, as written."""
    try:
        trees = [tree for tree in sqlglot.parse(sql, read=_DIALECT) if tree is not None]
    except Exception:  # noqa: BLE001 - unparseable: fall back to its identifier tokens
        return _token_identifiers(sql)
    names: set[str] = set()
    for tree in trees:
        names |= _tree_identifiers(tree)
    return names


def error_identifiers(text: str) -> set[str]:
    """Double-quoted identifiers in engine error text, split at dots."""
    names: set[str] = set()
    for match in _QUOTED_IDENT.finditer(text or ""):
        for part in match.group(1).replace('""', '"').split("."):
            part = part.strip()
            if part and len(part) <= _MAX_NAME and _WORD.fullmatch(part) and not part.isdigit():
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
    names: set[str] = set()
    for key in getattr(verified, "row_predicates", {}) or {}:
        for part in str(key).split("."):
            if part:
                names.add(fold_identifier(part))
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
    for sql in attempts.sql:
        candidates |= {fold_identifier(name) for name in sql_identifiers(sql)}
    for text in [*attempts.errors, *errors]:
        candidates |= {fold_identifier(name) for name in error_identifiers(text)}
    candidates = {name for name in candidates if name.strip() and len(name) <= _MAX_NAME}
    if not candidates:
        return NameScrub(frozenset())

    kept = _grant_names(verified) | {fold_identifier(str(n)) for n in caller_names if n}
    remaining = candidates - kept
    folded_texts = [fold_identifier(str(text)) for text in caller_texts if text]
    remaining = {name for name in remaining if not _in_text(name, folded_texts)}
    if remaining and verified is not None:
        from CortexOS.execution.submit import granted_column_names

        remaining -= granted_column_names(verified)
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


def scrub_answer(
    answer: Any,
    attempts: AttemptRecord,
    *,
    verified: Any,
    caller_texts: Iterable[str] = (),
    caller_names: Iterable[str] = (),
) -> Any:
    """``answer`` (a contract ``Answer``) with withheld names replaced in its free text."""
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
        update["reconfirm"] = answer.reconfirm.model_copy(
            update={
                "why": scrub.text(answer.reconfirm.why),
                "closest_question": scrub.text(answer.reconfirm.closest_question),
            }
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


__all__ = [
    "WITHHELD",
    "NameScrub",
    "ask_caller_context",
    "build_scrub",
    "error_identifiers",
    "scrub_answer",
    "scrub_http_detail",
    "sql_identifiers",
]
