"""C-SEC-3 proof tests — secrets scanner: clean tree passes, planted secrets caught."""
from __future__ import annotations

from scripts.secrets_scan import scan_repo, scan_text


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


def test_pattern_names_in_docs_do_not_trip():
    # Docs write the bare token names; only realistic full shapes may match.
    assert scan_text("scanner checks sk- and AKIA prefixes and PEM headers") == []
    assert scan_text("mentions ghs_ and ghp_ prefixes in prose") == []
