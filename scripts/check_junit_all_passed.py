"""Fail unless every expected test in a pytest junit XML file passed.

pytest exits 0 when every selected test is skipped. This checker closes that
gap for CI proof steps by requiring named test cases to be present and passed.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Report:
    ok: bool = False
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
    return case_name == expected or case_name.startswith(f"{expected}[")


def check(junit_path: Path, expected: list[str]) -> Report:
    report = Report()
    expected = list(dict.fromkeys(name.strip() for name in expected if name.strip()))
    if not expected:
        report.problems.append("no expected test names given (--expect is empty)")
        return report

    try:
        root = ET.parse(junit_path).getroot()
    except OSError as exc:
        report.problems.append(f"cannot read junit file {junit_path}: {exc}")
        report.missing = expected
        return report
    except ET.ParseError as exc:
        report.problems.append(f"junit file is not valid XML: {junit_path}: {exc}")
        report.missing = expected
        return report

    cases = [(case.get("name") or "", _outcome(case)) for case in root.iter("testcase")]
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

    report.missing = [
        name
        for name in expected
        if not any(_matches(case_name, name) for case_name, _ in cases)
    ]
    report.ok = not (
        report.problems
        or report.missing
        or report.skipped
        or report.failed
        or report.errored
    )
    return report


def _format_names(names: list[str]) -> str:
    return ", ".join(sorted(names)) if names else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", required=True, type=Path, help="pytest --junitxml output")
    parser.add_argument(
        "--expect",
        required=True,
        help="comma-separated test function names that must all pass",
    )
    args = parser.parse_args(argv)
    report = check(args.junit, args.expect.split(","))

    print(f"junit={args.junit}")
    print(f"passed={_format_names(report.passed)}")
    print(f"skipped={_format_names(report.skipped)}")
    print(f"failed={_format_names(report.failed)}")
    print(f"errored={_format_names(report.errored)}")
    print(f"missing={_format_names(report.missing)}")
    for problem in report.problems:
        print(f"problem: {problem}")

    if report.ok:
        print(f"OK: all {len(report.passed)} tests PASSED")
        return 0
    print("FAIL: proof did not run to PASSED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
