"""SCHEMA-RETRIEVE (#306) — relevant tables, columns and join paths before planning.

Deterministic first: identifier and description matches, ontology synonyms
(``sku``/``sku_id``/``sku_count``, item/product), warehouse names (``WH-A``,
``warehouse a``, a ``wh_a`` schema), glossary and metric phrases, and the
caller's Space table/formula memory (#290). Join paths come from a BFS over the
granted join graph. An optional :class:`Reranker` may reorder the granted
candidates; it can never add one.

Guards, each a single function so a guard-removed test can switch it off:

* :func:`_admit` — only tables the signed grant names are scored, stamped,
  returned or used as a join bridge; terms and join edges that touch any other
  table are dropped with them;
* :func:`_in_space` — a memory entry from another Space is dropped even when the
  memory port hands it over;
* :func:`_within` — a reranker's order is cut to the granted candidates.

Memory writes (``remember=True``) go through the #290 store, whose scored-pack
guard refuses any write tagged with a scored pack; the refusal code is stamped.

No model, no DB driver, no pack (import-linter contract 5).
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from cortex_contract.schema_retrieval import (
    RetrievalCandidate,
    RetrievedColumn,
    RetrievedJoinPath,
    RetrievedTable,
    SchemaRetrieval,
)

from CortexOS.memory.space_memory import MemoryEntry, MemoryRefused, MemoryStamp

ACTOR = "cortex:schema-retrieve"
METHOD = "deterministic"

# Ontology defaults. A catalog may add groups; it cannot remove these.
CONCEPT_SYNONYMS: Mapping[str, tuple[str, ...]] = {
    "sku": ("sku", "item", "product", "article", "skuid"),
    "warehouse": ("warehouse", "wh", "depot", "site", "dc", "location"),
    "supplier": ("supplier", "vendor"),
    "shipment": ("shipment", "delivery", "consignment"),
    "transaction": ("transaction", "txn", "movement"),
    "revenue": ("revenue", "sale", "sales"),
}

# Identifier parts too generic to say anything about a table on their own.
_GENERIC_PARTS = frozenset(
    {"id", "ids", "name", "code", "type", "date", "at", "is", "no", "num", "value", "key"}
)
_STOPWORDS = frozenset(
    """a an and are as at be by did do does for from had has have how i in is it its many
    me much my of on or our per show that the their them there these this those to was
    we were what when where which who why will with you your all any each list give tell
    get find total number count""".split()
)
_WORD = re.compile(r"[a-z0-9]+")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
# WH-A, WH A, wh_a, warehouse a, warehouse 3 -> wh_a / wh_3
_WAREHOUSE = re.compile(r"\b(?:wh|warehouse)[\s_\-]*([a-z]|\d{1,3})\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stem(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(_CAMEL.sub(r"\1 \2", text or "").lower())]


def _warehouse_codes(text: str) -> set[str]:
    flat = re.sub(r"[_.]", " ", (text or "").lower())
    return {f"wh_{m}" for m in _WAREHOUSE.findall(flat)}


def _ident(name: str) -> str:
    return "_".join(_words(name))


# -- grant and catalog ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GrantScope:
    """What one session may read: the signed grant's table keys and its Space.

    Build it from a *verified* manifest only (``VerifiedManifest.manifest``).
    """

    space_id: str
    tables: frozenset[str]

    @classmethod
    def from_manifest(cls, manifest: Any) -> GrantScope:
        keys = getattr(manifest, "row_predicates", None) or {}
        return cls(
            space_id=str(getattr(manifest, "space_id", "") or "").strip(),
            tables=frozenset(str(k).strip().lower() for k in keys if str(k).strip()),
        )

    def admits(self, table: str) -> bool:
        """Same matching as the manifest enforcer's grant key, on (schema, table).

        A qualified key grants exactly ``schema.table``; a bare key grants the
        default-schema table (bare or ``main.``). Three-part names never match.
        """
        key = table.strip().lower()
        parts = key.split(".")
        if len(parts) > 2 or not all(parts):
            return False
        if key in self.tables:
            return True
        return len(parts) == 2 and parts[0] == "main" and parts[1] in self.tables


@dataclass(frozen=True, slots=True)
class ColumnDoc:
    name: str
    description: str = ""
    visible: bool = True


@dataclass(frozen=True, slots=True)
class TableDoc:
    key: str
    columns: tuple[ColumnDoc, ...] = ()
    description: str = ""
    primary_key: str = ""
    row_count: int | None = None
    source: str = "catalog"
    memory_id: str | None = None

    def visible_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.visible]


@dataclass(frozen=True, slots=True)
class JoinEdge:
    left: str
    left_column: str
    right: str
    right_column: str

    def on(self) -> str:
        return f"{self.left}.{self.left_column} = {self.right}.{self.right_column}"


@dataclass(frozen=True, slots=True)
class Term:
    """A business phrase that points at a table (and optionally a column)."""

    phrase: str
    table: str
    column: str = ""
    origin: str = "glossary"


@dataclass(frozen=True, slots=True)
class SchemaCatalog:
    tables: Mapping[str, TableDoc]
    joins: tuple[JoinEdge, ...] = ()
    terms: tuple[Term, ...] = ()
    synonyms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    source: str = "catalog"
    pack_id: str | None = None


@dataclass(frozen=True, slots=True)
class Bounds:
    max_tables: int = 6
    max_columns: int = 12
    max_candidates: int = 24
    max_join_hops: int = 3
    # A table is chosen only if it scores at least this share of the top score.
    min_score_pct: int = 30

    def as_dict(self) -> dict[str, int]:
        return {
            "max_tables": self.max_tables,
            "max_columns": self.max_columns,
            "max_candidates": self.max_candidates,
            "max_join_hops": self.max_join_hops,
            "min_score_pct": self.min_score_pct,
        }


# -- ports ------------------------------------------------------------------


class MemoryPort(Protocol):
    """The #290 store surface retrieval uses. ``SpaceMemory`` satisfies it."""

    def read(
        self,
        *,
        space_id: str,
        kind: str,
        actor: str,
        key: str | None = None,
        scored_pack_id: str | None = None,
    ) -> tuple[list[MemoryEntry], MemoryStamp]: ...

    def write(
        self,
        *,
        space_id: str,
        kind: str,
        key: str,
        body: Mapping[str, Any],
        source: str,
        actor: str,
        pack_id: str | None = None,
        derived_from: Sequence[str] = (),
        validation: str | None = None,
        scored_pack_id: str | None = None,
    ) -> tuple[MemoryEntry, MemoryStamp]: ...


@dataclass(frozen=True, slots=True)
class Rerank:
    order: tuple[str, ...]
    ok: bool
    method: str
    reason: str = ""
    served_provider: str | None = None
    served_model: str | None = None


class Reranker(Protocol):
    """Reorders granted candidates. Sees only what :func:`_admit` let through."""

    def rerank(self, question: str, candidates: Sequence[TableDoc]) -> Rerank: ...


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """What a planner or prompt builder injects: the stamp plus the chosen docs."""

    stamp: SchemaRetrieval
    tables: tuple[TableDoc, ...]
    joins: tuple[JoinEdge, ...]

    def reduced_schema(self) -> dict[str, Any]:
        """Same shape as the L2 port's ``retrieve_schema`` (``schema_prompt_block``)."""
        return {
            "tables": {t.key: {"columns": t.visible_columns()} for t in self.tables},
            "joins": [
                {
                    "from_table": j.left,
                    "from_column": j.left_column,
                    "to_table": j.right,
                    "to_column": j.right_column,
                }
                for j in self.joins
            ],
            "top_k": [t.key for t in self.tables],
        }


class SchemaRetriever(Protocol):
    """Injectable seam for the #291 orchestrator and the #303 prompt builder."""

    def retrieve(
        self, question: str, *, grant: GrantScope, scored_pack_id: str | None = None
    ) -> RetrievalResult: ...


# -- guards -----------------------------------------------------------------


def _admit(catalog: SchemaCatalog, grant: GrantScope) -> SchemaCatalog:
    """Cut the catalog to the grant. Nothing outside it is scored or named."""
    tables = {k: v for k, v in catalog.tables.items() if grant.admits(k)}
    return replace(
        catalog,
        tables=tables,
        joins=tuple(j for j in catalog.joins if j.left in tables and j.right in tables),
        terms=tuple(t for t in catalog.terms if t.table in tables),
    )


def _in_space(entries: Iterable[MemoryEntry], space_id: str) -> list[MemoryEntry]:
    return [e for e in entries if e.space_id == space_id]


def _within(order: Sequence[str], allowed: Sequence[str]) -> list[str]:
    allowed_set = set(allowed)
    return [t for t in dict.fromkeys(order) if t in allowed_set]


# -- memory -> catalog --------------------------------------------------------


def _memory_columns(raw: Any) -> tuple[ColumnDoc, ...]:
    out: list[ColumnDoc] = []
    for item in raw or ():
        if isinstance(item, Mapping) and item.get("name"):
            out.append(ColumnDoc(str(item["name"]), str(item.get("description") or "")))
        elif isinstance(item, str) and item.strip():
            out.append(ColumnDoc(item.strip()))
    return tuple(out)


def _memory_joins(table: str, raw: Any) -> list[JoinEdge]:
    out: list[JoinEdge] = []
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        col = str(item.get("column") or item.get("from_column") or "")
        to_table = str(item.get("to_table") or "").lower()
        to_col = str(item.get("to_column") or "")
        if col and to_table and to_col:
            out.append(JoinEdge(table, col, to_table, to_col))
    return out


def _with_table_memory(catalog: SchemaCatalog, entries: Sequence[MemoryEntry]) -> SchemaCatalog:
    tables = dict(catalog.tables)
    joins = list(catalog.joins)
    for entry in entries:
        key = entry.key.strip().lower()
        if not key:
            continue
        body = entry.body
        keys = body.get("keys") or []
        mem_cols = _memory_columns(body.get("columns"))
        base = tables.get(key)
        if base is None:
            tables[key] = TableDoc(
                key=key,
                columns=mem_cols,
                description=str(body.get("description") or ""),
                primary_key=str(keys[0]) if keys else "",
                row_count=body.get("row_count") if isinstance(body.get("row_count"), int) else None,
                source=f"memory:{entry.id}",
                memory_id=entry.id,
            )
        else:
            known = {c.name.lower() for c in base.columns}
            extra = tuple(c for c in mem_cols if c.name.lower() not in known)
            tables[key] = replace(base, columns=base.columns + extra, memory_id=entry.id)
        joins.extend(_memory_joins(key, body.get("joins")))
    return replace(catalog, tables=tables, joins=tuple(dict.fromkeys(joins)))


_SQL_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _formula_hits(
    entries: Sequence[MemoryEntry], qwords: set[str]
) -> list[tuple[MemoryEntry, set[str], set[str]]]:
    """Formulas whose name is in the question: (entry, tables named, identifiers used)."""
    hits = []
    for entry in entries:
        name_parts = {w for w in _words(entry.key) if w not in _STOPWORDS}
        if not name_parts or not name_parts <= qwords:
            continue
        body = entry.body
        named = body.get("tables") or ([body["table"]] if body.get("table") else [])
        tables = {str(t).strip().lower() for t in named if str(t).strip()}
        idents = {_ident(i) for i in _SQL_IDENT.findall(str(body.get("definition") or ""))}
        hits.append((entry, tables, idents - {""}))
    return hits


# -- scoring ----------------------------------------------------------------


@dataclass(slots=True)
class _Score:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add(self, points: float, reason: str) -> None:
        self.score += points
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass(slots=True)
class _Question:
    words: set[str]
    idents: set[str]
    phrase: str
    synonyms: dict[str, str]
    warehouses: set[str]


def _question(text: str, groups: Mapping[str, tuple[str, ...]]) -> _Question:
    seq = _words(text)
    words = {w for w in seq if w not in _STOPWORDS}
    idents = set(seq) | {f"{a}_{b}" for a, b in zip(seq, seq[1:], strict=False)}
    synonyms: dict[str, str] = {}
    for head, members in groups.items():
        stems = {_stem(m) for m in (head, *members)}
        said = sorted(stems & set(seq))
        if said:
            for member in stems:
                synonyms.setdefault(member, said[0])
    return _Question(
        words=words,
        idents=idents,
        phrase=f" {' '.join(seq)} ",
        synonyms=synonyms,
        warehouses=_warehouse_codes(text),
    )


def _merge_groups(extra: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    groups = {k: tuple(v) for k, v in CONCEPT_SYNONYMS.items()}
    for head, members in extra.items():
        groups[head] = tuple(dict.fromkeys((*groups.get(head, ()), *members)))
    return groups


def _score_column(col: ColumnDoc, q: _Question) -> _Score:
    s = _Score()
    ident = _ident(col.name)
    parts = [p for p in ident.split("_") if p]
    if ident in q.idents and len(parts) > 1:
        s.add(3.0, f"column '{col.name}' named in question")
    for part in parts:
        if part in _GENERIC_PARTS or part in _STOPWORDS:
            continue
        if part in q.words:
            s.add(1.5, f"column part '{part}' in question")
        elif part in q.synonyms:
            s.add(0.75, f"ontology synonym '{q.synonyms[part]}' ~ '{part}'")
    return s


def _score_table(doc: TableDoc, q: _Question) -> _Score:
    s = _Score()
    ident = _ident(doc.key.rsplit(".", 1)[-1])
    for part in ident.split("_"):
        if not part or part in _GENERIC_PARTS or part in _STOPWORDS:
            continue
        if part in q.words:
            s.add(3.0, f"table name '{part}' in question")
        elif part in q.synonyms:
            s.add(1.5, f"ontology synonym '{q.synonyms[part]}' ~ '{part}'")
    codes = _warehouse_codes(doc.key) & q.warehouses
    for code in sorted(codes):
        s.add(4.0, f"names warehouse {code.upper().replace('_', '-')}")
    desc_hits = sorted({w for w in _words(doc.description) if len(w) > 2} & q.words)
    if desc_hits:
        s.add(min(2.0, 0.5 * len(desc_hits)), f"description mentions {', '.join(desc_hits[:3])}")
    return s


def _bfs(start: str, goal: str, adj: Mapping[str, list[JoinEdge]], max_hops: int) -> list[JoinEdge]:
    seen = {start}
    queue: deque[tuple[str, list[JoinEdge]]] = deque([(start, [])])
    while queue:
        node, path = queue.popleft()
        if node == goal:
            return path
        if len(path) >= max_hops:
            continue
        for edge in adj.get(node, ()):
            nxt = edge.right if edge.left == node else edge.left
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, [*path, edge]))
    return []


def _adjacency(joins: Iterable[JoinEdge]) -> dict[str, list[JoinEdge]]:
    adj: dict[str, list[JoinEdge]] = {}
    for edge in joins:
        adj.setdefault(edge.left, []).append(edge)
        adj.setdefault(edge.right, []).append(edge)
    for edges in adj.values():
        edges.sort(key=lambda e: (e.left, e.left_column, e.right, e.right_column))
    return adj


# -- retriever --------------------------------------------------------------


class DeterministicSchemaRetriever:
    """Lexical + ontology + memory + join-graph retrieval inside one grant and Space."""

    def __init__(
        self,
        catalog: SchemaCatalog | Callable[[], SchemaCatalog],
        *,
        memory: MemoryPort | None = None,
        remember: bool = False,
        reranker: Reranker | None = None,
        bounds: Bounds | None = None,
        actor: str = ACTOR,
    ) -> None:
        self._catalog = catalog
        self._memory = memory
        self._remember = remember
        self._reranker = reranker
        self._bounds = bounds or Bounds()
        self._actor = actor

    def _load(self) -> SchemaCatalog:
        return self._catalog() if callable(self._catalog) else self._catalog

    def _read_memory(self, space: str, kind: str, scored_pack_id: str | None) -> list[MemoryEntry]:
        if self._memory is None or not space:
            return []
        entries, _ = self._memory.read(
            space_id=space, kind=kind, actor=self._actor, scored_pack_id=scored_pack_id
        )
        return _in_space(entries, space)

    def retrieve(
        self, question: str, *, grant: GrantScope, scored_pack_id: str | None = None
    ) -> RetrievalResult:
        bounds = self._bounds
        space = grant.space_id
        base = self._load()
        table_mem = self._read_memory(space, "table", scored_pack_id)
        formula_mem = self._read_memory(space, "formula", scored_pack_id)
        catalog = _admit(_with_table_memory(base, table_mem), grant)
        q = _question(question, _merge_groups(catalog.synonyms))

        table_scores: dict[str, _Score] = {}
        column_scores: dict[str, dict[str, _Score]] = {}
        for key in sorted(catalog.tables):
            doc = catalog.tables[key]
            ts = _score_table(doc, q)
            cols: dict[str, _Score] = {}
            for col in doc.columns:
                if not col.visible:
                    continue
                cs = _score_column(col, q)
                if cs.score > 0:
                    cols[col.name] = cs
            table_scores[key] = ts
            column_scores[key] = cols

        for term in catalog.terms:
            words = " ".join(_words(term.phrase))
            if not words or f" {words} " not in q.phrase:
                continue
            reason = f"{term.origin} '{term.phrase}'"
            if term.column and any(
                c.name == term.column and c.visible for c in catalog.tables[term.table].columns
            ):
                column_scores[term.table].setdefault(term.column, _Score()).add(3.0, reason)
            table_scores[term.table].add(2.5, reason)

        used_formulas: list[str] = []
        for entry, named, idents in _formula_hits(formula_mem, q.words):
            reason = f"formula memory '{entry.key}'"
            for key in sorted(catalog.tables):
                doc = catalog.tables[key]
                cols = [c.name for c in doc.columns if c.visible and _ident(c.name) in idents]
                if key in named or cols:
                    table_scores[key].add(2.5, reason)
                    used_formulas.append(entry.id)
                    for name in cols:
                        column_scores[key].setdefault(name, _Score()).add(2.0, reason)

        totals: dict[str, float] = {}
        for key, ts in table_scores.items():
            codes = _warehouse_codes(key)
            if q.warehouses and codes and not codes & q.warehouses:
                totals[key] = 0.0  # another warehouse's table
                continue
            top_cols = sorted((c.score for c in column_scores[key].values()), reverse=True)[:3]
            totals[key] = round(ts.score + sum(top_cols), 4)
        ranked = sorted((k for k, v in totals.items() if v > 0), key=lambda k: (-totals[k], k))
        candidates = ranked[: bounds.max_candidates]

        method, reason, provider, model = METHOD, "", None, None
        if not candidates:
            reason = "no granted table matched the question"
        elif self._reranker is not None:
            rr = self._reranker.rerank(question, [catalog.tables[k] for k in candidates])
            if rr.ok:
                picked = _within(rr.order, candidates)
                candidates = picked + [k for k in candidates if k not in picked]
                method = rr.method
            else:
                reason = f"rerank unavailable: {rr.reason}"
            provider, model = rr.served_provider, rr.served_model

        floor = max(totals.values(), default=0.0) * bounds.min_score_pct / 100.0
        chosen = [k for k in candidates if totals.get(k, 0.0) >= floor][: bounds.max_tables]
        adj = _adjacency(catalog.joins)
        paths: list[RetrievedJoinPath] = []
        path_edges: list[JoinEdge] = []
        bridges: dict[str, str] = {}
        if chosen:
            anchor = chosen[0]
            for other in chosen[1:]:
                hops = _bfs(anchor, other, adj, bounds.max_join_hops)
                if not hops:
                    continue
                walk = [anchor]
                for edge in hops:
                    walk.append(edge.right if edge.left == walk[-1] else edge.left)
                for mid in walk[1:-1]:
                    if mid not in chosen:
                        bridges.setdefault(mid, f"bridge on join path {' -> '.join(walk)}")
                path_edges.extend(hops)
                paths.append(
                    RetrievedJoinPath(
                        tables=walk,
                        on=[e.on() for e in hops],
                        reason=f"shortest granted join path {anchor} -> {other}",
                    )
                )
        for mid, why in bridges.items():
            table_scores[mid].add(0.0, why)
        chosen_all = chosen + [m for m in bridges if m not in chosen]
        join_cols: dict[str, set[str]] = {}
        for edge in path_edges:
            join_cols.setdefault(edge.left, set()).add(edge.left_column)
            join_cols.setdefault(edge.right, set()).add(edge.right_column)

        chosen_out: list[RetrievedTable] = []
        for key in chosen_all:
            doc = catalog.tables[key]
            cols = column_scores[key]
            ordered = sorted(cols, key=lambda n: (-cols[n].score, n))
            out_cols = [
                RetrievedColumn(
                    name=n, score=round(cols[n].score, 4), reasons=list(cols[n].reasons)
                )
                for n in ordered
            ]
            visible = {c.name for c in doc.columns if c.visible}
            for name in sorted(join_cols.get(key, set()) - set(ordered)):
                if name in visible:
                    out_cols.append(RetrievedColumn(name=name, score=0.0, reasons=["join key"]))
            if (
                doc.primary_key
                and doc.primary_key in visible
                and all(c.name != doc.primary_key for c in out_cols)
            ):
                out_cols.append(
                    RetrievedColumn(name=doc.primary_key, score=0.0, reasons=["primary key"])
                )
            chosen_out.append(
                RetrievedTable(
                    table=key,
                    score=totals.get(key, 0.0),
                    reasons=list(table_scores[key].reasons),
                    source=doc.source,
                    columns=out_cols[: bounds.max_columns],
                )
            )

        mem_read = [e.id for e in table_mem if e.key.strip().lower() in catalog.tables] + list(
            dict.fromkeys(used_formulas)
        )
        written, refusals = self._write_back(catalog, chosen_all, space, scored_pack_id)

        stamp = SchemaRetrieval(
            served_space_id=space,
            served_method=method,
            served_candidates=[
                RetrievalCandidate(table=k, score=totals.get(k, 0.0)) for k in candidates
            ],
            served_chosen=chosen_out,
            served_join_paths=paths,
            served_memory_ids_read=list(dict.fromkeys(mem_read)),
            served_memory_ids_written=written,
            served_memory_refusals=refusals,
            served_bounds=bounds.as_dict(),
            served_at=_now(),
            served_by=self._actor,
            served_reason=reason,
            served_provider=provider,
            served_model=model,
        )
        return RetrievalResult(
            stamp=stamp,
            tables=tuple(catalog.tables[k] for k in chosen_all),
            joins=tuple(dict.fromkeys(path_edges)),
        )

    def _write_back(
        self,
        catalog: SchemaCatalog,
        chosen: Sequence[str],
        space: str,
        scored_pack_id: str | None,
    ) -> tuple[list[str], list[str]]:
        """Learn chosen catalog tables into the Space's table memory (#290 store)."""
        if not self._remember or self._memory is None or not space:
            return [], []
        written: list[str] = []
        refusals: list[str] = []
        for key in chosen:
            doc = catalog.tables[key]
            if doc.memory_id is not None:
                continue
            body = {
                "columns": doc.visible_columns(),
                "keys": [doc.primary_key] if doc.primary_key else [],
                "joins": [
                    {"column": j.left_column, "to_table": j.right, "to_column": j.right_column}
                    for j in catalog.joins
                    if j.left == key
                ],
                "row_count": doc.row_count,
            }
            try:
                entry, _ = self._memory.write(
                    space_id=space,
                    kind="table",
                    key=key,
                    body=body,
                    source=f"schema_retrieve:{catalog.source}",
                    actor=self._actor,
                    pack_id=catalog.pack_id,
                    scored_pack_id=scored_pack_id,
                )
            except MemoryRefused as exc:
                if exc.code not in refusals:
                    refusals.append(exc.code)
                continue
            written.append(entry.id)
        return written, refusals
