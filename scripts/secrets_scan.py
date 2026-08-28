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

# GitHub tokens are opaque strings (Apr/May 2026 changelog): installation /
# Actions ``ghs_`` tokens may be classic (~40) or JWT-shaped (~520 chars).
# Do not pin a max length — only require the known prefix + non-trivial body.
_GH_OPAQUE = r"[A-Za-z0-9._-]{20,}"

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_pat", re.compile(rf"\bghp_{_GH_OPAQUE}\b")),
    ("github_installation_token", re.compile(rf"\bghs_{_GH_OPAQUE}\b")),
    ("github_oauth_token", re.compile(rf"\bgho_{_GH_OPAQUE}\b")),
    ("github_refresh_token", re.compile(rf"\bghr_{_GH_OPAQUE}\b")),
    ("github_fine_grained_pat", re.compile(rf"\bgithub_pat_{_GH_OPAQUE}\b")),
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

# Reading a whole file was unbounded: tracked corpora (.jsonl, .csv) reach tens
# of megabytes, and read_text() on one of those raised MemoryError on a loaded
# machine. The scanner then died with a traceback having scanned nothing, which
# is not a gate (KB R-0007). Anything past the limit is streamed instead.
WHOLE_READ_LIMIT = 4 * 1024 * 1024
CHUNK_CHARS = 1 << 20
# Carried into the next chunk so a secret lying across a chunk boundary is
# still matched. Must exceed the longest match any pattern above can produce.
OVERLAP_CHARS = 4096


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


def scan_file(path: Path) -> list[tuple[str, str]]:
    """Findings for one file, never holding more than a chunk in memory."""
    try:
        if path.stat().st_size <= WHOLE_READ_LIMIT:
            return scan_text(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return []
    except MemoryError:
        pass  # fall through to the streaming path rather than dying
    return _scan_streaming(path)


def _scan_streaming(path: Path) -> list[tuple[str, str]]:
    """Scan a large file in overlapping chunks.

    A finding seen in the overlap would otherwise be reported twice, so hits
    are de-duplicated. If the read cannot be finished, that is reported as a
    finding rather than returning a short list that reads as "clean" - a scan
    that silently stopped early is exactly the lie this gate exists to prevent
    (KB R-0011).
    """
    seen: set[tuple[str, str]] = set()
    found: list[tuple[str, str]] = []
    tail = ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            while True:
                block = handle.read(CHUNK_CHARS)
                if not block:
                    break
                for hit in scan_text(tail + block):
                    if hit not in seen:
                        seen.add(hit)
                        found.append(hit)
                tail = block[-OVERLAP_CHARS:]
    except (OSError, MemoryError) as exc:
        found.append(("unscannable_file", f"{type(exc).__name__}..."))
    return found


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
        for name, snippet in scan_file(path):
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
