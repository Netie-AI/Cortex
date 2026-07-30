#!/usr/bin/env python3
"""Prove every contract minor differs from its predecessor only additively.

A minor bump that removes a field, narrows a type, or adds a required field is a
breaking change wearing a minor's version number — and it breaks consumers at
runtime, in their language, far from this repo. This script reads the frozen
specs listed in ``contract/compat.yaml`` and classifies every difference.

Additive (a minor may do this):
  * a new schema, a new property, a new endpoint, a new enum value
  * an optional property becoming... still optional
  * a property becoming *less* required

Breaking (needs a major):
  * removing a schema, property, endpoint or enum value a consumer may read
  * changing a property's type
  * adding to ``required`` — an old producer omits it and is now invalid
  * removing a deprecated field before the major it was promised for

Run with no arguments to check the whole chain. Exit 1 on any breaking change,
and say which, rather than bumping anything automatically.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "contract"

# The contract surface is exactly these operationIds — kept in lockstep with
# CortexOS.api.contract_routes.CONTRACT_ROUTE_IDS and scripts/export_openapi.py.
CONTRACT_ROUTE_IDS: frozenset[str] = frozenset(
    {"ask", "submit", "ledger.append", "ledger.verify", "tool.registry"}
)


@dataclass
class Report:
    additive: list[str] = field(default_factory=list)
    breaking: list[str] = field(default_factory=list)

    def add(self, note: str) -> None:
        self.additive.append(note)

    def brk(self, note: str) -> None:
        self.breaking.append(note)


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a mapping")
    return data


def _schemas(spec: dict[str, Any]) -> dict[str, Any]:
    return dict((spec.get("components") or {}).get("schemas") or {})


def _operations(spec: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.startswith("x-") or method == "parameters":
                continue
            if isinstance(op, dict):
                out.add(f"{method.upper()} {path} ({op.get('operationId', '?')})")
    return out


def _type_of(prop: Any) -> str:
    """A comparable shape for one property, tolerant of the ways pydantic emits it."""
    if not isinstance(prop, dict):
        return repr(prop)
    for key in ("$ref", "type"):
        if key in prop:
            base = str(prop[key])
            if key == "type" and prop.get("type") == "array":
                return f"array<{_type_of(prop.get('items'))}>"
            return base
    # anyOf/oneOf: an optional field is anyOf[T, null]. Compare as a set so
    # member order never reads as a change.
    for key in ("anyOf", "oneOf", "allOf"):
        if key in prop and isinstance(prop[key], list):
            return key + "{" + ",".join(sorted(_type_of(m) for m in prop[key])) + "}"
    return "unknown"


def _compare_schema(name: str, old: dict[str, Any], new: dict[str, Any], report: Report) -> None:
    old_props = dict(old.get("properties") or {})
    new_props = dict(new.get("properties") or {})

    for prop in sorted(set(old_props) - set(new_props)):
        report.brk(f"{name}.{prop} was removed; a consumer reading it breaks")
    for prop in sorted(set(new_props) - set(old_props)):
        report.add(f"{name}.{prop} added ({_type_of(new_props[prop])})")

    for prop in sorted(set(old_props) & set(new_props)):
        was, now = _type_of(old_props[prop]), _type_of(new_props[prop])
        if was != now:
            report.brk(f"{name}.{prop} changed type: {was} -> {now}")

    old_req = set(old.get("required") or [])
    new_req = set(new.get("required") or [])
    for prop in sorted(new_req - old_req):
        report.brk(
            f"{name}.{prop} became required; a producer built against the older "
            "spec omits it and is now invalid"
        )
    for prop in sorted(old_req - new_req):
        report.add(f"{name}.{prop} is no longer required")

    old_enum, new_enum = set(old.get("enum") or []), set(new.get("enum") or [])
    for value in sorted(old_enum - new_enum):
        report.brk(f"{name} dropped enum value {value!r}")
    for value in sorted(new_enum - old_enum):
        report.add(f"{name} added enum value {value!r}")


def compare(old_spec: dict[str, Any], new_spec: dict[str, Any]) -> Report:
    report = Report()

    old_schemas, new_schemas = _schemas(old_spec), _schemas(new_spec)
    for name in sorted(set(old_schemas) - set(new_schemas)):
        report.brk(f"schema {name} was removed")
    for name in sorted(set(new_schemas) - set(old_schemas)):
        report.add(f"schema {name} added")
    for name in sorted(set(old_schemas) & set(new_schemas)):
        _compare_schema(name, old_schemas[name], new_schemas[name], report)

    old_ops, new_ops = _operations(old_spec), _operations(new_spec)
    for op in sorted(old_ops - new_ops):
        report.brk(f"operation removed: {op}")
    for op in sorted(new_ops - old_ops):
        report.add(f"operation added: {op}")

    return report


def _check_digest(spec_path: Path) -> str | None:
    sidecar = spec_path.with_suffix(spec_path.suffix + ".sha256")
    if not sidecar.is_file():
        return f"{sidecar.name} is missing; a vendored copy has nothing to verify against"
    recorded = sidecar.read_text(encoding="utf-8").split()[0].strip()
    actual = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    if recorded != actual:
        return f"{sidecar.name} records {recorded[:12]}… but the spec hashes to {actual[:12]}…"
    return None


def main() -> int:
    compat = _load_yaml(CONTRACT_DIR / "compat.yaml")
    supported = compat.get("supported") or []
    if len(supported) < 1:
        print("compat.yaml lists no supported versions", file=sys.stderr)
        return 1

    problems: list[str] = []
    specs: list[tuple[str, dict[str, Any]]] = []

    for entry in supported:
        version, filename = str(entry["version"]), str(entry["spec"])
        path = CONTRACT_DIR / filename
        if not path.is_file():
            problems.append(f"{version}: {filename} is listed in compat.yaml but not on disk")
            continue
        digest_problem = _check_digest(path)
        if digest_problem:
            problems.append(f"{version}: {digest_problem}")
        specs.append((version, json.loads(path.read_text(encoding="utf-8"))))

    if problems:
        print("contract compatibility FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    # Entries flagged contract_surface: false are retained artifacts, not
    # compatibility references — see compat.yaml. They are excluded here rather
    # than compared, because a spec with no contract operations reports every
    # contract operation as "added" and every engine route as "removed", which
    # buries the schema changes that actually matter.
    surfaces = [
        (str(e["version"]), str(e["spec"]))
        for e in supported
        if e.get("contract_surface", True)
    ]
    surface_versions = {v for v, _ in surfaces}
    for version, spec in specs:
        if version not in surface_versions:
            print(f"{version}: retained artifact, not a contract surface — skipped")
            continue
        found = {
            op.split("(")[-1].rstrip(")") for op in _operations(spec)
        }
        if found != set(CONTRACT_ROUTE_IDS):
            missing = sorted(set(CONTRACT_ROUTE_IDS) - found)
            extra = sorted(found - set(CONTRACT_ROUTE_IDS))
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if extra:
                # Truncated: a mis-exported full engine table is ~169 entries and
                # printing them all buries the one line that matters.
                shown = extra[:5]
                suffix = f" (+{len(extra) - len(shown)} more)" if len(extra) > len(shown) else ""
                detail.append(f"{len(extra)} unexpected, e.g. {shown}{suffix}")
            problems.append(
                f"{version}: declares contract_surface but does not publish the allowlist — "
                + "; ".join(detail)
            )
    if problems:
        print("contract compatibility FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    specs = [(v, s) for v, s in specs if v in surface_versions]

    breaking_found = False
    for (old_v, old_spec), (new_v, new_spec) in zip(specs, specs[1:], strict=False):
        report = compare(old_spec, new_spec)
        print(f"\n{old_v} -> {new_v}: {len(report.additive)} additive, {len(report.breaking)} breaking")
        for note in report.additive:
            print(f"  + {note}")
        for note in report.breaking:
            print(f"  ! {note}")
        if report.breaking:
            breaking_found = True

    if breaking_found:
        print(
            "\nFAILED — a minor bump cannot carry a breaking change. Either restore "
            "compatibility (deprecate alongside instead of removing) or cut a new major "
            "with a coordinated consumer release.",
            file=sys.stderr,
        )
        return 1

    versions = ", ".join(v for v, _ in specs)
    print(f"\nOK — every step additive across {versions}; digests agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
