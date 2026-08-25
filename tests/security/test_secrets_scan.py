"""C-SEC-3 proof tests — secrets scanner: clean tree passes, planted secrets caught."""
from __future__ import annotations

from pathlib import Path

from scripts.secrets_scan import (
    CHUNK_CHARS,
    WHOLE_READ_LIMIT,
    _scan_streaming,
    scan_file,
    scan_repo,
    scan_text,
)

# A realistic shape, planted on purpose. This file is in SELF_ALLOW precisely so
# the scanner's own fixtures do not trip it.
_PLANTED = "sk-" + "abcdefghijklmnopqrstuvwx123456"


def test_clean_tree_has_no_findings():
    violations = scan_repo()
    assert violations == [], f"tracked secrets or forbidden files found: {violations}"


def test_planted_secrets_detected():
    fixture = "\n".join([
        "config = {",
        "  'openai': 'sk-abcdefghijklmnopqrstuvwx123456',",
        "  'aws': 'AKIAIOSFODNN7EXAMPLE',"
        "  'gh': 'ghp_" + "a" * 36 + "',",
        "}",
        "-----BEGIN RSA PRIVATE KEY-----",
        'DMS_MASTER_KEY="' + "A" * 44 + '"',
    ])
    names = {name for name, _ in scan_text(fixture)}
    assert {"openai_style_key", "aws_access_key", "github_pat",
            "private_key_pem", "master_key_assignment"} <= names


def test_github_installation_token_opaque_long_jwt_shape():
    """GitHub App / Actions ghs_ tokens may be ~520-char JWT-shaped (opaque).

    See https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header/
    and the Apr 2026 notice — never assume classic 40-char length.
    """
    # Realistic-ish long body (prefix + opaque payload); not a real token.
    body = "1_" + ("A" * 100) + "." + ("B" * 200) + "." + ("C" * 200)
    token = "ghs_" + body
    assert len(token) > 400
    names = {name for name, _ in scan_text(f"Authorization: Bearer {token}")}
    assert "github_installation_token" in names


def test_github_classic_ghs_still_detected():
    classic = "ghs_" + ("x" * 36)
    names = {name for name, _ in scan_text(classic)}
    assert "github_installation_token" in names


def test_demo_keys_are_not_findings():
    assert scan_text("X-API-Key: dms-demo-steward-key") == []


def test_a_small_file_is_scanned_whole(tmp_path: Path):
    small = tmp_path / "small.py"
    small.write_text(f"key = {_PLANTED!r}\n", encoding="utf-8")
    assert any(name == "openai_style_key" for name, _ in scan_file(small))


def test_a_file_too_big_to_read_whole_is_still_scanned(tmp_path: Path):
    """The gate used to die with MemoryError on a multi-megabyte tracked file.

    Reading whole is now capped, and anything larger is streamed. The secret is
    placed across a chunk boundary because that is the case a naive chunked
    read silently misses - and a scanner that misses is worse than one that
    crashes, because it reports clean.
    """
    big = tmp_path / "big.jsonl"
    head = "x" * (CHUNK_CHARS - 11) + " "  # planted secret starts at CHUNK_CHARS-10
    filler = " " + "y" * (WHOLE_READ_LIMIT + 1024)
    big.write_text(head + _PLANTED + filler, encoding="utf-8")
    assert big.stat().st_size > WHOLE_READ_LIMIT, "fixture must exceed the whole-read cap"
    assert any(name == "openai_style_key" for name, _ in scan_file(big))


def test_the_chunk_overlap_does_not_double_report(tmp_path: Path):
    big = tmp_path / "big.jsonl"
    head = "x" * (CHUNK_CHARS - 11) + " "
    big.write_text(head + _PLANTED + " " + "y" * (CHUNK_CHARS * 2), encoding="utf-8")
    hits = [h for h in _scan_streaming(big) if h[0] == "openai_style_key"]
    assert len(hits) == 1, f"overlap region reported the same secret twice: {hits}"


def test_an_unreadable_file_is_reported_not_skipped(tmp_path: Path):
    """A scan that stopped early must not read as clean (KB R-0011)."""
    missing = tmp_path / "gone.jsonl"
    findings = _scan_streaming(missing)
    assert findings and findings[0][0] == "unscannable_file"


def test_pattern_names_in_docs_do_not_trip():
    # Docs write the bare token names; only realistic full shapes may match.
    assert scan_text("scanner checks sk- and AKIA prefixes and PEM headers") == []
    assert scan_text("mentions ghs_ and ghp_ prefixes in prose") == []
