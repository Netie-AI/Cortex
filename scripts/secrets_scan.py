"""C-SEC-3 — repo secrets scanner. Fails (exit 1) if a real-looking secret is tracked.

Scans git-tracked text files (or only staged files with --staged) for
high-confidence secret shapes. Demo keys (``dms-demo-*-key``) are expected and
ignored. Docs may NAME the patterns (e.g. the string "AKIA") without tripping
the scan — patterns require realistic full shapes.

Usage:
  python -m scripts.secrets_scan            # scan all tracked files
  python -m scripts.secrets_scan --staged   # pre-commit / CI gate on the index
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("slack_token", re.compile(r"\bxox[bap]-[A-Za-z0-9-]{10,}\b")),
    ("age_secret_key", re.compile(r"\bAGE-SECRET-KEY-1[A-Z0-9]{20,}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("master_key_assignment",
     re.compile(r"DMS_MASTER_KEY\s*[=:]\s*[\"']?[A-Za-z0-9+/]{40,}={0,2}[\"']?")),
)

# Files that legitimately contain pattern TEXT (the scanner itself, its test).
SELF_ALLOW = {"scripts/secrets_scan.py", "tests/security/test_secrets_scan.py"}

# Files that must never be tracked at all.
FORBIDDEN_TRACKED = {"env.local", "key.md"}

TEXT_EXT = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".ps1", ".bat",
            ".sql", ".js", ".jsx", ".ts", ".tsx", ".cfg", ".ini", ".env", ".example",
            ".sh", ".csv", ".jsonl", ".html", ".css"}


def _git_files(staged: bool) -> list[str]:
    cmd = ["git", "diff", "--cached", "--name-only"] if staged else ["git", "ls-files"]
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def scan_text(text: str) -> list[tuple[str, str]]:
    """Return (pattern_name, matched_snippet) findings for one blob of text."""
    findings: list[tuple[str, str]] = []
    for name, pattern in PATTERNS:
        for match in pattern.finditer(text):
            snippet = match.group(0)
            if "dms-demo-" in snippet:
                continue
            findings.append((name, snippet[:12] + "..."))
    return findings


def scan_repo(*, staged: bool = False) -> list[str]:
    """Return human-readable violations. Empty list = clean."""
    violations: list[str] = []
    files = _git_files(staged)

    for forbidden in FORBIDDEN_TRACKED:
        if any(f == forbidden or f.endswith("/" + forbidden) for f in files):
            violations.append(f"FORBIDDEN tracked file: {forbidden}")

    for rel in files:
        if rel in SELF_ALLOW:
            continue
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, snippet in scan_text(text):
            violations.append(f"{rel}: {name} ({snippet})")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true",
                        help="scan only files staged in the index")
    args = parser.parse_args()
    violations = scan_repo(staged=args.staged)
    if violations:
        print("SECRETS SCAN FAILED:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("secrets scan clean (0 findings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
