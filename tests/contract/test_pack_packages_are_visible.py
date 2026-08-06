"""A directory of modules that is not a package is invisible to the boundary check.

C2-01. `lint-imports` reported "2 kept, 0 broken" while blind to 13 crossings of
hard invariant #1 — the engine importing a pack — on `answer_engine.py`, the
hottest module in the repo. The cause was not the checker: `packs/dms/semantic/`
had no `__init__.py`, so grimp never built a node for it and the import edges
did not exist as far as the contract was concerned.

That is the worst failure mode a gate has. Not "it found nothing", but "it
cannot see, and reports the same green either way" (R-0007).

So this asserts the precondition rather than the conclusion: every directory
under `packs/` that holds importable modules must be a package. Without it the
next directory added without an `__init__.py` silently reopens the hole, and
the contract keeps saying green.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / "packs"

#: Directories that legitimately hold no importable Python.
_IGNORED = {"__pycache__", "data", "semantic_layer", "ontology_yaml"}


def _module_dirs() -> list[Path]:
    """Directories under packs/ containing at least one .py file."""
    out: list[Path] = []
    for path in PACKS.rglob("*.py"):
        parent = path.parent
        if any(part in _IGNORED for part in parent.parts):
            continue
        if parent not in out:
            out.append(parent)
    return sorted(out)


def test_every_pack_module_directory_is_a_package() -> None:
    """No `__init__.py` means grimp cannot see it, and the C2 contract goes blind."""
    missing = [
        d.relative_to(ROOT).as_posix()
        for d in _module_dirs()
        if not (d / "__init__.py").is_file()
    ]

    assert not missing, (
        "these directories hold modules but are not packages, so the import "
        "boundary cannot see anything they import:\n"
        + "\n".join(f"  {m}  → add {m}/__init__.py" for m in missing)
    )


def test_the_scan_actually_finds_directories() -> None:
    """Guard the guard: a scan that matches nothing passes forever."""
    found = _module_dirs()

    assert len(found) > 5, f"only found {len(found)} pack module dirs — the scan is broken"
    assert any(d.name == "semantic" for d in found), (
        "packs/dms/semantic is the directory whose invisibility caused C2-01; "
        "if the scan no longer sees it, the scan is wrong"
    )
