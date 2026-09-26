"""Fail unless every expected test in a pytest junit XML file PASSED.

pytest exits 0 when every selected test is skipped (for example when a DSN is
unset or an optional dependency is missing). A CI proof step that only checks
pytest's exit code can therefore go green having proved nothing. This checker
closes that gap: it reads the junit file and exits 1 unless each name given to
``--expect`` is present and passed (not skipped, failed or errored).

Usage::

    python scripts/check_junit_all_passed.py --junit out.xml --expect test_a,test_b

An expected name matches a testcase whose ``name`` equals it, or a
parametrised instance ``name[...]``. A parametrised name must have every
instance passed. Any unexpected testcase in the file that did not pass also
fails the check, so a newly added test in the same module cannot silently skip.

Exit codes: 0 all expected passed; 1 anything else (including an empty suite,
a missing or unreadable junit file, or an empty ``--expect`` list).
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Report:
    ok: bool
    passed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _outcome(case: ET.Element) -> str:
    if case.find("error") is not None:
        return "error"
    if case.find("failure") is not None:
        return "failure"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


def _matches(case_name: str, expected: str) -> bool:
    return case_name == expected or case_name.startswith(expected + "[")


def check(junit_path: Path, expected: list[str]) -> Report:
    report = Report(ok=False)
    expected = [e.strip() for e in expected if e.strip()]
    if not expected:
        report.problems.append("no expected test names given (--expect is empty)")
        return report
    try:
        root = ET.parse(junit_path).getroot()
    except FileNotFoundError:
        report.problems.append(f"junit file not found: {junit_path}")
        report.missing = sorted(expected)
        return report
    except ET.ParseError as exc:
        report.problems.append(f"junit file is not valid XML: {junit_path}: {exc}")
        report.missing = sorted(expected)
        return report

    cases = [(c.get("name") or "", _outcome(c)) for c in root.iter("testcase")]
    if not cases:
        report.problems.append("junit file contains no testcases (empty suite)")

    buckets = {
        "passed": report.passed,
        "skipped": report.skipped,
        "failure": report.failed,
        "error": report.errored,
    }
    for name, outcome in cases:
        buckets[outcome].append(name)

    for exp in expected:
        if not any(_matches(name, exp) for name, _ in cases):
            report.missing.append(exp)

    report.ok = not (
        report.problems or report.missing or report.skipped or report.failed or report.errored
    )
    return report


def _fmt(names: list[str]) -> str:
    return ", ".join(sorted(names)) if names else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", required=True, type=Path, help="pytest --junitxml output")
    parser.add_argument(
        "--expect",
        required=True,
        help="comma-separated test function names that must all be PASSED",
    )
    args = parser.parse_args(argv)
    report = check(args.junit, args.expect.split(","))

    print(f"junit={args.junit}")
    print(f"passed={_fmt(report.passed)}")
    print(f"skipped={_fmt(report.skipped)}")
    print(f"failed={_fmt(report.failed)}")
    print(f"errored={_fmt(report.errored)}")
    print(f"missing={_fmt(report.missing)}")
    for problem in report.problems:
        print(f"problem: {problem}")
    if report.ok:
        print(f"OK: all {len(report.passed)} tests PASSED")
        return 0
    print("FAIL: proof did not run to PASSED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
