#!/usr/bin/env python3
"""Assert independent version lines for engine vs cortex_contract.

Checks:
1. ``packages/cortex_contract/version.py`` CONTRACT_VERSION matches the
   packaged version in ``packages/cortex_contract/pyproject.toml``.
2. Engine version in root ``pyproject.toml`` / ``CortexOS.__version__`` is
   consistent.
3. No source file assumes engine version == contract version.
4. Installed distribution metadata for the contract, and the version the
   importable ``cortex_contract`` actually reports, both agree with the source.

Check 4 exists because this script used to pass while ``pip show
cortex-contract`` said 1.1.0 against a tree whose CONTRACT_VERSION was 1.3.0.
Metadata is what a consumer resolves a pin against, so stale dist-info is a pin
that means something other than what it says - and the CONTRACT-01 hazard is
exactly this: the bare name resolving to a stale install while the tree is what
was edited, inside the one function whose bytes DMS signs.

This script reports. It never installs, upgrades or repairs anything.
"""

from __future__ import annotations

import ast
import re
import sys
from importlib.metadata import PackageNotFoundError, distribution, packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Printed, never run. Repairing a developer's environment behind their back is
#: how a check stops being evidence of anything.
REINSTALL = "python -m pip install --no-deps --force-reinstall -e packages/cortex_contract"


def _toml_project_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # Prefer [project] version= before [tool.poetry] to avoid ambiguity.
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r'^version\s*=\s*"([^"]+)"', stripped)
            if m:
                return m.group(1)
    raise SystemExit(f"No [project].version in {path}")


def _contract_version_py() -> str:
    path = ROOT / "packages" / "cortex_contract" / "version.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONTRACT_VERSION":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise SystemExit(f"CONTRACT_VERSION not found in {path}")


def _engine_version_py() -> str:
    import CortexOS

    return str(CortexOS.__version__)


#: The contract's own distribution. Only this one's *version* means the contract
#: version; see contract_dist_versions.
_CONTRACT_DIST = "cortex-contract"


def contract_dist_versions() -> dict[str, str]:
    """The contract's own installed distribution, and its version.

    Discovered through the metadata rather than assumed, so a dist whose RECORD
    is stale is still probed by name.

    **Only the contract's own distribution is compared.** ``netie`` bundles the
    same module (``pyproject.toml`` includes ``cortex_contract`` from
    ``packages/``), and it is tempting to check it too - a stale bundled copy is
    a real hazard. But a bundling distribution's version is the *engine*
    version, and engine and contract version lines are independent by hard
    invariant: 2.5.0 tracks G-gates, 1.3.0 tracks the wire. Requiring the netie
    dist to report the contract version would assert engine == contract, which
    is the exact coupling check 3 of this same script exists to forbid. The
    script would then contradict itself, and CI would go red on a tree that is
    correct.

    The hazard it was reaching for is real and is caught elsewhere, better:
    ``imported_contract()`` reads CONTRACT_VERSION off whichever module actually
    loads. A stale bundled copy shadowing the source fails there, on evidence
    from the import system rather than from a version number that was never
    claiming to be the contract's.
    """
    out: dict[str, str] = {}
    for name in {*packages_distributions().get("cortex_contract", []), _CONTRACT_DIST}:
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue
        resolved = dist.metadata["Name"] or name
        if str(resolved).strip().lower().replace("_", "-") != _CONTRACT_DIST:
            continue
        out[resolved] = dist.version
    return out


def imported_contract() -> tuple[str | None, str | None]:
    """(version, file) of the ``cortex_contract`` this interpreter actually loads."""
    try:
        import cortex_contract
    except ImportError:
        return None, None
    return str(cortex_contract.CONTRACT_VERSION), str(getattr(cortex_contract, "__file__", "") or "")


def contract_metadata_errors(
    source_version: str,
    dist_versions: dict[str, str],
    imported_version: str | None,
    imported_file: str | None,
) -> list[str]:
    """Compare installed metadata and the loaded module against the source.

    Kept pure so it can be exercised both ways. A comparator only ever seen
    against one environment is a comparator nobody has watched fail.
    """
    errors: list[str] = []

    for dist_name, dist_version in sorted(dist_versions.items()):
        # Only the contract's own distribution carries the contract version. A
        # distribution that merely bundles the module carries the ENGINE
        # version, and holding it to this number would assert engine ==
        # contract - the coupling check 3 below exists to forbid. Filtered here
        # as well as in the lookup so the property holds for any caller, not
        # just the one that happens to feed this today.
        if str(dist_name).strip().lower().replace("_", "-") != _CONTRACT_DIST:
            continue
        if dist_version != source_version:
            errors.append(
                f"installed distribution {dist_name!r} reports {dist_version!r} but "
                f"packages/cortex_contract/version.py says {source_version!r}. "
                f"pip metadata is what a consumer resolves a pin against, so this "
                f"pin means something other than what it says.\n"
                f"      fix your local editable install by running, from the repo root:\n"
                f"      {REINSTALL}"
            )

    if imported_version is None:
        errors.append(
            "'import cortex_contract' failed - the contract is not importable under "
            "its distributed name, so no consumer pin resolves.\n"
            f"      {REINSTALL}"
        )
    elif imported_version != source_version:
        errors.append(
            f"the importable cortex_contract reports {imported_version!r} but this "
            f"tree says {source_version!r} (loaded from {imported_file}). A stale "
            f"copy is shadowing the source - CONTRACT-01's exact hazard, in the one "
            f"function whose bytes DMS signs.\n"
            f"      {REINSTALL}"
        )

    return errors


# Patterns that incorrectly couple the two version lines.
_COUPLING_RES = [
    re.compile(r"CONTRACT_VERSION\s*==\s*.*__version__"),
    re.compile(r"__version__\s*==\s*.*CONTRACT_VERSION"),
    re.compile(r"engine.?version.*=.*contract.?version", re.I),
    re.compile(r"contract.?version.*=.*engine.?version", re.I),
    re.compile(r"assert\s+.*CONTRACT_VERSION\s*==\s*.*2\.5\.0"),
    re.compile(r"assert\s+.*__version__\s*==\s*.*CONTRACT_VERSION"),
]


def _scan_coupling() -> list[str]:
    offenders: list[str] = []
    roots = [
        ROOT / "CortexOS",
        ROOT / "packages",
        ROOT / "packs",
        ROOT / "scripts",
        ROOT / "tests",
    ]
    skip_names = {"check_versions.py"}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name in skip_names:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if "CONTRACT_VERSION" not in line and "contract version" not in line.lower():
                    continue
                for rx in _COUPLING_RES:
                    if rx.search(line):
                        offenders.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    return offenders


def main() -> int:
    contract_py = _contract_version_py()
    contract_pkg = _toml_project_version(ROOT / "packages" / "cortex_contract" / "pyproject.toml")
    engine_toml = _toml_project_version(ROOT / "pyproject.toml")
    engine_py = _engine_version_py()

    errors: list[str] = []
    if contract_py != contract_pkg:
        errors.append(
            f"contract mismatch: version.py={contract_py!r} pyproject={contract_pkg!r}"
        )
    if engine_toml != engine_py:
        errors.append(
            f"engine mismatch: CortexOS.__version__={engine_py!r} pyproject={engine_toml!r}"
        )
    if contract_py == engine_py:
        # Independent lines may coincidentally match in the far future; forbid
        # treating that as a single shared constant today by requiring they differ
        # OR are explicitly documented. Policy: they must not be assumed equal —
        # if they happen to be equal numerically, still OK as long as no code
        # couples them. Only fail the scan below.
        pass

    coupled = _scan_coupling()
    if coupled:
        errors.append("engine/contract versions assumed equal:\n  " + "\n  ".join(coupled))

    dist_versions = contract_dist_versions()
    imported_version, imported_file = imported_contract()
    if not dist_versions:
        # R-0011: say so in the output, not in a log line nobody reads. A check
        # that quietly compares nothing reports the same green as one that did.
        print(
            "NOTE  no installed distribution ships 'cortex_contract' - dist metadata "
            "not compared. The loaded module's version is still checked."
        )
    errors.extend(
        contract_metadata_errors(contract_py, dist_versions, imported_version, imported_file)
    )

    if errors:
        print("check_versions FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    installed = ", ".join(f"{n}=={v}" for n, v in sorted(dist_versions.items())) or "none"
    print(f"OK  engine={engine_py} contract={contract_py} (independent version lines)")
    print(f"    installed dists: {installed}")
    print(f"    loaded contract: {imported_version} from {imported_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
