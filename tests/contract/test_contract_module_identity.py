"""CONTRACT-01 — one file must not be two module identities.

``cortex_contract`` (the distributed name, what DMS pins) and
``packages.cortex_contract`` (the in-tree path) both resolved to
``packages/cortex_contract/execution.py`` while producing *different classes*:

    cortex_contract.execution.Manifest is packages.cortex_contract.execution.Manifest
    -> False

That matters in exactly one place, and it is the worst place. ``
canonical_manifest_bytes`` branches on ``isinstance(manifest, Manifest)``:
model instances take ``model_dump(mode="json")``, anything else falls to
``dict(manifest)``. Under two identities the isinstance check silently returns
False, so a Manifest built with one spelling is canonicalised through the
Mapping branch meant for raw dicts.

The bytes agreed anyway — by luck. Every Manifest field is ``str``,
``list[str]``, ``dict[str, str]`` or ``None``, and for those two branches
coincide. Add one ``datetime``, ``UUID`` or nested model and they diverge:
``dict(model)`` keeps a ``datetime`` object, ``model_dump(mode="json")``
produces an ISO string.

DMS signs those bytes and Cortex verifies them, so the divergence would
present as a signature failure — a crypto bug, not a serialisation bug — and
CLAUDE.md §4 already names this the most dangerous function in the repo.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import importlib
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from cortex_contract.execution import Manifest, canonical_manifest_bytes
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]

NOW = dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=dt.timezone.utc)
TRACE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")

#: The distributed name. The only spelling that may appear in an import.
DISTRIBUTED = "cortex_contract"
#: The in-tree path spelling. Same file, second module identity, which is the
#: whole defect. Assembled rather than written literally so this constant is not
#: itself a hit for the scans below.
IN_TREE = "packages." + DISTRIBUTED

#: Where source lives. `build/` and `dist/` are setuptools copies of the
#: contract package, not source, and `pip install -e` recreates them.
_SCAN_ROOTS = ("CortexOS", "packs", "packages", "scripts", "netie", "bench", "tests")
_SKIP_PARTS = {"__pycache__", "build", "dist", ".venv", "node_modules", ".git"}


def _manifest(**over: object) -> Manifest:
    base: dict[str, object] = {
        "session_id": "sess-1",
        "org_id": "acme",
        "pool_id": "pool-a",
        "issuer_key_id": "int-1",
        "allowed_paths": ["/data/pool/acme/**"],
        "row_predicates": {"orders": "TRUE"},
        "issued_at": NOW.isoformat(),
        "expires_at": NOW.isoformat(),
        "signature": "sig",
    }
    base.update(over)
    return Manifest(**base)  # type: ignore[arg-type]


class _StampedManifest(Manifest):
    """A Manifest whose fields are not all strings.

    Every field on the real Manifest today is ``str`` / ``list[str]`` /
    ``dict[str, str]`` / ``None``, and for those the two canonicalisation
    branches coincide. That is the accident the ticket calls luck, so a test
    built on the plain helper cannot see the divergence it is meant to catch.
    A datetime and a UUID break the coincidence: ``dict(model)`` hands
    ``json.dumps`` objects it cannot serialise, ``model_dump(mode="json")``
    hands it strings.
    """

    stamped_at: dt.datetime = NOW
    trace_id: uuid.UUID = TRACE_ID


def _stamped_manifest() -> _StampedManifest:
    return _StampedManifest(**_manifest().model_dump())


def _source_files() -> list[Path]:
    """Every tracked-looking .py under the scan roots, copies excluded."""
    out: list[Path] = []
    for name in _SCAN_ROOTS:
        root = ROOT / name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in rel.parts):
                continue
            out.append(path)
    return out


def _is_contract_module(name: str) -> bool:
    return any(name == base or name.startswith(base + ".") for base in (DISTRIBUTED, IN_TREE))


def _contract_imports(paths: list[Path] | None = None) -> dict[str, list[str]]:
    """Every contract module imported anywhere in source -> where.

    Derived from the AST rather than from a list of known call sites. A list
    is only ever as complete as the day it was written, and the defect this
    file exists for was two *function-local* imports that no list mentioned.
    """
    found: dict[str, list[str]] = {}
    for path in paths if paths is not None else _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - not our business here
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            elif isinstance(node, ast.Call):
                names = _dynamic_import_target(node)
            for name in names:
                if _is_contract_module(name):
                    where = f"{path.relative_to(ROOT).as_posix()}:{getattr(node, 'lineno', 0)}"
                    found.setdefault(name, []).append(where)
    return found


def _dynamic_import_target(node: ast.Call) -> list[str]:
    """A string module name handed to importlib.import_module / __import__.

    Matching the call rather than the bare string keeps prose, docstrings and
    assertion messages out of the scan - they name the forbidden spelling on
    purpose, and a scan that flags them is a scan someone switches off.
    """
    func = node.func
    fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if fname not in {"import_module", "__import__"}:
        return []
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return [node.args[0].value]
    return []


def _lazy_contract_modules(path: Path) -> list[str]:
    """Contract modules imported from inside a function body in one file.

    Importing the module does not execute these, which is exactly why the
    sys.modules guard used to be blind to them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and inner.module and inner.level == 0:
                if _is_contract_module(inner.module):
                    out.append(inner.module)
            elif isinstance(inner, ast.Import):
                out.extend(a.name for a in inner.names if _is_contract_module(a.name))
    return sorted(set(out))


def _in_tree_entries_loaded() -> list[str]:
    return sorted(m for m in sys.modules if m == IN_TREE or m.startswith(IN_TREE + "."))


def test_only_one_spelling_of_the_contract_is_imported() -> None:
    """The whole defect in one assertion.

    Importing the engine must not leave both names loaded. Two entries here is
    two classes, and two classes is the isinstance branch below going wrong.

    Importing the engine's two entry points is not enough on its own.
    ``drillthrough.py`` imports the contract *inside* its functions, so after
    that pair ``CortexOS.execution.drillthrough`` is not even in sys.modules and
    the lazy spelling has never run. Both lazy sites are forced below: one by
    calling the public function that performs the import for real, and the rest
    by importing every spelling the file's AST says it uses.
    """
    import CortexOS.execution.drillthrough as drill
    import CortexOS.execution.manifest  # noqa: F401  - the engine's entry point
    import CortexOS.execution.submit  # noqa: F401

    # Runs the function-local import in manifest_content_hash for real.
    drill.manifest_content_hash(_manifest())

    lazy = _lazy_contract_modules(ROOT / "CortexOS" / "execution" / "drillthrough.py")
    assert lazy, (
        "drillthrough.py no longer imports the contract inside a function - if "
        "the lazy imports are gone this guard is watching nothing (update it); "
        "if they moved, point it at the new file"
    )
    refused: list[str] = []
    for module in lazy:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            refused.append(f"{module}: {exc}")

    both = DISTRIBUTED in sys.modules and IN_TREE in sys.modules
    assert not (both or refused or _in_tree_entries_loaded()), (
        f"the contract is loaded under more than one name: "
        f"both={both} in_tree_entries={_in_tree_entries_loaded()} refused={refused}\n"
        f"lazy imports executed: {lazy}\n"
        f"one file, two classes. Import '{DISTRIBUTED}' everywhere, including "
        f"inside functions."
    )


def test_isinstance_holds_for_a_manifest_the_engine_built() -> None:
    """If this is False, canonicalisation silently takes the Mapping branch."""
    from CortexOS.execution import manifest as engine_manifest

    built = _manifest()
    assert isinstance(built, engine_manifest.Manifest)


def test_canonical_bytes_agree_across_engine_call_sites() -> None:
    """Both production call sites must canonicalise identically.

    ``drillthrough`` imports the function inside its functions, so this reaches
    for the same spelling those local imports use — that spelling *is* the
    thing under test.
    """
    import importlib

    drill_mod = importlib.import_module(_drillthrough_contract_module())
    from CortexOS.execution.manifest import canonical_manifest_bytes as manifest_bytes

    built = _manifest()
    assert (
        drill_mod.canonical_manifest_bytes(built)
        == manifest_bytes(built)
        == canonical_manifest_bytes(built)
    )


def _drillthrough_contract_module() -> str:
    """Which contract spelling drillthrough.py actually imports.

    Read from the AST, and anchored at the repo root rather than the working
    directory - a helper that resolves against cwd passes for the wrong reason
    when cwd happens to be right.
    """
    lazy = _lazy_contract_modules(ROOT / "CortexOS" / "execution" / "drillthrough.py")
    hits = [m for m in lazy if m.endswith(".execution")]
    assert hits, "drillthrough.py no longer imports the contract - update this test"
    assert len(set(hits)) == 1, f"drillthrough.py imports the contract under {hits}"
    return hits[0]


def test_non_string_field_canonicalises_through_the_model_branch() -> None:
    """The latent hazard, made explicit.

    Today every Manifest field is a string, so the two branches coincide and the
    divergence is invisible. This pins the *rule* rather than that accident: a
    model must canonicalise through ``model_dump(mode="json")``, so a field that
    is not already a string is serialised the JSON way.

    Without it, the first non-string field added to the contract silently
    changes the signed bytes.
    """

    class StampedManifest(Manifest):
        stamped_at: dt.datetime = NOW

    built = StampedManifest(
        session_id="s",
        org_id="o",
        pool_id="p",
        issuer_key_id="k",
        allowed_paths=["/x/**"],
        row_predicates={"t": "TRUE"},
        issued_at=NOW.isoformat(),
        expires_at=NOW.isoformat(),
        signature="sig",
    )

    raw = canonical_manifest_bytes(built).decode("utf-8")
    assert "2026-08-06T12:00:00Z" in raw, (
        "a datetime field did not canonicalise as an ISO string — this went "
        "through dict(model), not model_dump(mode='json')"
    )


def test_mapping_branch_still_serves_real_mappings() -> None:
    """R-0005: tightening the model branch must not refuse legitimate dict input."""
    as_dict = _manifest().model_dump(mode="json")

    assert canonical_manifest_bytes(as_dict) == canonical_manifest_bytes(_manifest())


def test_dict_and_json_dump_differ_for_non_strings() -> None:
    """The mechanism, asserted directly, so the reason above cannot rot.

    If pydantic ever made these identical, the tests here would still pass while
    testing nothing. This fails loudly if the premise stops holding.
    """

    class Demo(BaseModel):
        when: dt.datetime

    demo = Demo(when=NOW)
    assert dict(demo) != demo.model_dump(mode="json")
    with pytest.raises(TypeError):
        import json

        json.dumps(dict(demo))


def _canonical_or_fail(fn: Callable[..., bytes], manifest: object, where: str) -> bytes:
    """Canonicalise, turning the divergence into a sentence instead of a traceback."""
    try:
        return fn(manifest)
    except TypeError as exc:
        pytest.fail(
            f"{where} could not canonicalise a Manifest with a non-string field: {exc}\n"
            f"That is the Mapping branch - dict(model) - running on a model, which "
            f"means the Manifest class this call site sees is not the class the "
            f"manifest was built from. One file, two module identities (CONTRACT-01)."
        )


def test_non_string_manifest_agrees_across_both_call_sites() -> None:
    """The acceptance clause of CONTRACT-01, asserted as the conjunction it is.

    WHEN a Manifest containing a non-string field is passed through BOTH
    canonical_manifest_bytes call sites THE SYSTEM SHALL produce identical bytes.

    Two halves, and every existing test had only one of them.
    ``test_canonical_bytes_agree_across_engine_call_sites`` reaches both call
    sites with the all-string helper - the case that agrees by luck.
    ``test_non_string_field_canonicalises_through_the_model_branch`` uses a
    datetime but only one call site. Neither can see a divergence that needs a
    non-string field *and* two call sites to appear.

    ``manifest_content_hash`` is included because it is the production entry
    point that performs the lazy import itself: the two module-level references
    above could agree while the call the engine actually makes did something
    else.
    """
    from CortexOS.execution import drillthrough as drill_site
    from CortexOS.execution.manifest import canonical_manifest_bytes as manifest_site

    drill_contract = importlib.import_module(_drillthrough_contract_module())
    built = _stamped_manifest()

    via_manifest = _canonical_or_fail(manifest_site, built, "CortexOS/execution/manifest.py")
    via_drill = _canonical_or_fail(
        drill_contract.canonical_manifest_bytes, built, "CortexOS/execution/drillthrough.py"
    )

    assert via_drill == via_manifest, (
        "the two call sites canonicalise the same Manifest to different bytes:\n"
        f"  manifest.py    : {via_manifest!r}\n"
        f"  drillthrough.py: {via_drill!r}\n"
        "DMS signs these bytes and Cortex verifies them, so this presents as a "
        "signature failure, not a serialisation bug."
    )

    assert drill_site.manifest_content_hash(built) == hashlib.sha256(via_manifest).hexdigest(), (
        "drillthrough.manifest_content_hash disagrees with the verifier's own "
        "canonicalisation - the lazy import inside that function resolves to a "
        "different contract module"
    )

    text = via_manifest.decode("utf-8")
    assert '"stamped_at":"2026-08-06T12:00:00Z"' in text, (
        f"datetime did not canonicalise as an ISO string: {text}"
    )
    assert f'"trace_id":"{TRACE_ID}"' in text, f"UUID did not canonicalise as a string: {text}"


def test_no_source_file_imports_the_in_tree_spelling() -> None:
    """Static guard: the spelling must not come back, anywhere, in any form.

    The unification is a fact about today's tree, not a property of it. Nothing
    stopped the spelling returning - ``packages/`` is importable as a namespace
    package from the repo root, so the wrong import compiles, runs, and produces
    the right bytes right up until a field stops being a string.

    Scans function-local imports and importlib.import_module string arguments
    too. The two imports that caused this ticket were function-local.
    """
    offenders = {
        module: where
        for module, where in _contract_imports().items()
        if module == IN_TREE or module.startswith(IN_TREE + ".")
    }

    assert not offenders, (
        f"'{IN_TREE}' is imported in source - that is the same file under a "
        f"second module identity, and isinstance against the contract's Manifest "
        f"silently returns False inside canonical_manifest_bytes:\n"
        + "\n".join(f"  {m}  at {', '.join(w)}" for m, w in sorted(offenders.items()))
        + f"\nImport '{DISTRIBUTED}' instead."
    )


def test_the_import_scan_actually_finds_imports() -> None:
    """Guard the guard: a scan that matches nothing passes forever."""
    found = _contract_imports()

    assert found, "the contract import scan found nothing - it is broken"
    assert len(_source_files()) > 200, (
        f"only {len(_source_files())} source files scanned - the walk is broken"
    )
    assert f"{DISTRIBUTED}.execution" in found, (
        f"the scan cannot see '{DISTRIBUTED}.execution', which manifest.py "
        f"imports at module level - it is not reading imports"
    )
    planted = ROOT / "tests" / "contract" / "conftest_does_not_exist.py"
    assert _contract_imports([planted] if planted.exists() else []) == {}


def test_the_in_tree_spelling_refuses_to_import() -> None:
    """Runtime guard, in the package itself, because source is not the only caller.

    A static scan covers tracked files. It does not cover a notebook, an
    untracked script, a debugger session, or a `python -c` in CI - and any of
    those loading the second identity is enough to mint bytes that will not
    verify. So the package refuses to be loaded under any name but its
    distributed one, which closes the class rather than the known call sites.

    Run out of process on purpose: importing the forbidden spelling here to
    prove it fails would leave its submodules in this interpreter's sys.modules
    and quietly break the guard two tests up, depending on collection order.
    """
    proc = subprocess.run(
        [sys.executable, "-c", f"import {IN_TREE}"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert proc.returncode != 0, (
        f"'{IN_TREE}' still imports. Same file, second module identity - "
        f"the guard in packages/cortex_contract/__init__.py is not firing.\n"
        f"stdout: {proc.stdout}"
    )
    assert "CONTRACT-01" in proc.stderr, (
        f"it failed, but not for our reason - the message must name the defect "
        f"so the next reader is not left guessing:\n{proc.stderr}"
    )


def test_the_distributed_spelling_still_imports() -> None:
    """R-0005: the guard must refuse the wrong spelling, not the work.

    Out of process for the same reason, and because an in-process assertion
    would be satisfied by this module's own import at the top of the file.
    """
    proc = subprocess.run(
        [sys.executable, "-c", f"import {DISTRIBUTED} as c; print(c.CONTRACT_VERSION)"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert proc.returncode == 0, (
        f"the guard refuses the distributed spelling too - it has broken every "
        f"consumer:\n{proc.stderr}"
    )
    assert proc.stdout.strip(), "imported, but CONTRACT_VERSION printed nothing"
