"""C4 remainder — who is allowed to open DuckDB, ratcheted.

CLAUDE.md states the invariant plainly: ``duckdb`` appears only under
``CortexOS/execution/``, so that one place opens the database and one place can
enforce a manifest. It is written down and nothing checks it — ``.importlinter``
declares two contracts and neither is about duckdb — so the rule has been true
by habit rather than by construction.

It is currently false in two places, both inside one file, and both predate
this test. ``scripts/lakehouse_migrate.py`` was the third and is now fixed: it
goes through ``execution.warehouse.get_connection``, which matters beyond
tidiness — opening the warehouse read-write behind that helper's back skipped
the cached-reader eviction and produced DuckDB's "Can't open a connection to
same database file with a different configuration" mid-suite.

This is deliberately **not** an allowlist. An allowlist absorbs new entries
silently, which is how ``_C2_ALLOWLIST`` became pre-C2 debt that had to be
evacuated. This asserts the violation set matches *exactly*, so it fails in both
directions:

* a fourth site opening duckdb -> fails, new debt is caught at the commit
* one of the three fixed -> fails, and the fix is finished by deleting its row

The honest fix for ``packs/dms/lakehouse/catalog.py`` is not a one-line swap:
it opens an in-memory DuckDB and ``ATTACH``es a DuckLake catalog, which is a
different connection shape from ``execution.warehouse.get_connection``. Moving
it means deciding whether the lakehouse is a second legitimate opener or whether
lakehouse connection management belongs inside ``CortexOS/execution`` too - an
architecture call, with manifest consequences, not a refactor to do in passing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The one directory permitted to import duckdb.
SANCTIONED = "CortexOS/execution"

SEARCH_ROOTS = ("CortexOS", "packs", "scripts", "bench")

#: Known openers outside the sanctioned directory, with why each is still here.
#: Delete a row when it is fixed - the test fails until you do.
KNOWN_VIOLATIONS: dict[str, str] = {
    "packs/dms/lakehouse/catalog.py": (
        "opens an in-memory DuckDB and ATTACHes the DuckLake catalog; needs an "
        "architecture decision about whether the lakehouse plane is a second "
        "sanctioned opener before it can move under CortexOS/execution"
    ),
}


def _duckdb_importers() -> dict[str, list[int]]:
    """Every file importing duckdb, by repo-relative posix path -> line numbers."""
    found: dict[str, list[int]] = {}
    for search_root in SEARCH_ROOTS:
        base = ROOT / search_root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "__pycache__" in rel or "/.venv" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (SyntaxError, UnicodeDecodeError):
                continue
            lines: list[int] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name.split(".")[0] == "duckdb" for a in node.names):
                        lines.append(node.lineno)
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "duckdb":
                        lines.append(node.lineno)
            if lines:
                found[rel] = sorted(lines)
    return found


@pytest.fixture(scope="module")
def offenders() -> dict[str, list[int]]:
    return {
        rel: lines
        for rel, lines in _duckdb_importers().items()
        if not rel.startswith(f"{SANCTIONED}/")
    }


def test_the_sanctioned_directory_actually_opens_duckdb() -> None:
    """Guard the guard: if nothing under execution/ imports duckdb, the scan broke.

    A boundary test that silently matches nothing reports clean forever (R-0007).
    """
    inside = [rel for rel in _duckdb_importers() if rel.startswith(f"{SANCTIONED}/")]
    assert inside, f"no duckdb import found under {SANCTIONED}/ — the AST scan is not working"


def test_no_new_duckdb_openers_appear(offenders: dict[str, list[int]]) -> None:
    """A fourth place opening the database is new debt, caught here."""
    unexpected = sorted(set(offenders) - set(KNOWN_VIOLATIONS))
    assert not unexpected, (
        "new duckdb import outside "
        f"{SANCTIONED}/ — route it through CortexOS.execution so the manifest "
        "still applies, or add it to KNOWN_VIOLATIONS with a reason:\n"
        + "\n".join(f"  {rel}:{offenders[rel]}" for rel in unexpected)
    )


def test_fixed_violations_are_removed_from_the_ledger(
    offenders: dict[str, list[int]],
) -> None:
    """The other direction: a row that no longer describes reality is a lie too."""
    stale = sorted(set(KNOWN_VIOLATIONS) - set(offenders))
    assert not stale, (
        "these no longer import duckdb — delete them from KNOWN_VIOLATIONS "
        f"to finish the fix:\n" + "\n".join(f"  {rel}" for rel in stale)
    )


def test_every_known_violation_carries_a_reason() -> None:
    blank = [rel for rel, why in KNOWN_VIOLATIONS.items() if not why.strip()]
    assert not blank, f"known violations need a stated reason: {blank}"
