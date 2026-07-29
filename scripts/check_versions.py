#!/usr/bin/env python3
"""Assert independent version lines for engine vs cortex_contract.

Checks:
1. ``packages/cortex_contract/version.py`` CONTRACT_VERSION matches the
   packaged version in ``packages/cortex_contract/pyproject.toml``.
2. Engine version in root ``pyproject.toml`` / ``CortexOS.__version__`` is
   consistent.
3. No source file assumes engine version == contract version.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

    if errors:
        print("check_versions FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        f"OK — engine={engine_py} contract={contract_py} "
        f"(independent version lines)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
