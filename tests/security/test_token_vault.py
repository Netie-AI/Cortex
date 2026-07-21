"""Reversible token-vault: mask is safe to egress, unmask restores locally."""

from __future__ import annotations

from packs.dms.security.token_vault import TokenVault, mask_text


SAMPLE = "Email john.doe@acme.com and NRIC S1234567D; ping john.doe@acme.com again."


def test_mask_removes_raw_pii_and_is_reversible() -> None:
    vault = TokenVault(salt=b"fixed-test-salt.")
    res = vault.mask(SAMPLE)
    # no raw PII survives into the maskable payload
    assert "john.doe@acme.com" not in res.masked
    assert "S1234567D" not in res.masked
    assert "NETIE_EMAIL_" in res.masked
    assert "NETIE_NRIC_" in res.masked
    # repeated value -> same token (model sees a consistent reference)
    assert res.masked.count(vault._token_for("email", "john.doe@acme.com")) == 2
    # round-trip through a model that echoes tokens verbatim
    model_reply = res.masked.replace("Email", "Contact")
    assert "john.doe@acme.com" in vault.unmask(model_reply)
    assert "S1234567D" in vault.unmask(res.masked)


def test_tokens_are_session_scoped() -> None:
    a = TokenVault(salt=b"salt-A-aaaaaaaaa")
    b = TokenVault(salt=b"salt-B-bbbbbbbbb")
    ta = a.mask("mail me@x.com").masked
    tb = b.mask("mail me@x.com").masked
    assert ta != tb  # different salts -> non-linkable tokens


def test_seal_unseal_roundtrip() -> None:
    vault = TokenVault(salt=b"fixed-test-salt.")
    vault.mask(SAMPLE)
    key, blob = vault.seal()
    restored = TokenVault.unseal(key, blob)
    # the sealed map can still unmask
    masked = vault.mask("reach john.doe@acme.com").masked
    assert "john.doe@acme.com" in restored.unmask(masked)


def test_purge_clears_plaintext() -> None:
    res, vault = mask_text(SAMPLE)
    assert vault.audit_summary()["tokens"] >= 2
    vault.purge()
    assert vault.audit_summary()["tokens"] == 0
    # unmask is now a no-op (map gone) — tokens stay tokens
    assert "NETIE_" in vault.unmask(res.masked)


def test_audit_summary_has_no_plaintext() -> None:
    _, vault = mask_text(SAMPLE)
    summary = vault.audit_summary()
    assert "john.doe@acme.com" not in str(summary)
    assert set(summary["by_kind"]) <= {"email", "nric", "credit_card", "phone"}
