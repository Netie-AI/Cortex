"""F7 security hardening tests (PII choke-point, envelope crypto)."""

from __future__ import annotations

import base64
import os

import pytest

from packs.dms.security.crypto import decrypt_field, encrypt_field
from packs.dms.security.pii import redact_for_prompt


@pytest.fixture
def master_key_env(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("DMS_MASTER_KEY", key)
    return key


def test_pii_redacted_before_prompt(monkeypatch):
    """Critical: raw PII must not reach JudgmentModel via the NL query choke-point."""
    from CortexOS.dms.query_service import answer_question
    from CortexOS.routing.judgment_model import JudgmentModel

    captured: list[str] = []
    original = JudgmentModel.decide

    def spy_decide(self, req):
        captured.append(req.content)
        return original(self, req)

    monkeypatch.setattr(JudgmentModel, "decide", spy_decide)

    raw_nric = "S1234567A"
    raw_phone = "91234567"
    raw_email = "john.doe@example.com"
    question = (
        f"List low stock for contact {raw_nric}, phone {raw_phone}, email {raw_email}"
    )

    answer_question(question)

    assert captured, "JudgmentModel.decide was never called — choke-point not exercised"
    for content in captured:
        assert raw_nric not in content
        assert raw_email not in content
        assert "[REDACTED:nric]" in content
        assert "[REDACTED:email]" in content
        assert "[REDACTED:phone]" in content


def test_encrypt_decrypt_roundtrip(master_key_env):
    del master_key_env
    plaintext = "sensitive warehouse note — NRIC on file"
    blob = encrypt_field(plaintext)
    assert decrypt_field(blob) == plaintext
    assert plaintext not in blob


def test_redact_preserves_non_pii():
    text = "Which SKUs are below reorder level in warehouse WH-A?"
    assert redact_for_prompt(text) == text

    mixed = "Low stock at WH-B; alert severity HIGH"
    assert redact_for_prompt(mixed) == mixed
