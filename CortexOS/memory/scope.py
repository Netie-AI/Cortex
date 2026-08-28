"""C6 — scope tags and subset retrieval (architecture §11).

Every memory entry carries the scope that wrote it. Retrieval must keep only
entries where ``entry_scope ⊆ session_scope`` **in the storage query**, never as
a post-filter after ranking forbidden rows.

Tags are opaque strings (``tenant:…``, ``space:…``, ``personal``, ``company``).
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize_scope(tags: Iterable[str] | None) -> frozenset[str]:
    """Canonical tag set: strip, drop empties, freeze."""
    if not tags:
        return frozenset()
    return frozenset(t.strip() for t in tags if t and str(t).strip())


def scope_subseteq(entry_scope: Iterable[str], session_scope: Iterable[str]) -> bool:
    """True iff every entry tag is present in the session scope."""
    return normalize_scope(entry_scope) <= normalize_scope(session_scope)


def space_tag(space_id: str | None) -> str | None:
    """Canonical Space tag, or None when unbound."""
    sid = (space_id or "").strip()
    return f"space:{sid}" if sid else None


def tenant_tag(tenant_id: str | None) -> str | None:
    tid = (tenant_id or "").strip()
    return f"tenant:{tid}" if tid else None


def session_scope_from(
    *,
    space_id: str | None = None,
    tenant_id: str | None = None,
    extra: Iterable[str] | None = None,
) -> frozenset[str]:
    """Build the caller's session scope from Space / tenant / extras."""
    tags: list[str] = []
    st = space_tag(space_id)
    if st:
        tags.append(st)
    tt = tenant_tag(tenant_id)
    if tt:
        tags.append(tt)
    if extra:
        tags.extend(extra)
    return normalize_scope(tags)


def sql_entry_subseteq_session(
    *,
    tags_table: str = "scope_tags",
    record_id_col: str = "r.id",
    session_tags: frozenset[str],
) -> tuple[str, list[str]]:
    """SQL fragment: entry tags ⊆ session tags via NOT EXISTS (storage-side).

    Returns ``(clause, bind_args)``. Empty ``session_tags`` yields a constant
    false predicate (fail closed — no unscoped session may read scoped memory).
    Untagged rows are also excluded (must carry at least one scope tag).
    """
    if not session_tags:
        return ("0", [])
    placeholders = ",".join("?" * len(session_tags))
    args = sorted(session_tags)
    clause = (
        f"EXISTS (SELECT 1 FROM {tags_table} _se WHERE _se.record_id = {record_id_col}) "
        f"AND NOT EXISTS ("
        f"SELECT 1 FROM {tags_table} _st "
        f"WHERE _st.record_id = {record_id_col} "
        f"AND _st.tag NOT IN ({placeholders})"
        f")"
    )
    return clause, args
