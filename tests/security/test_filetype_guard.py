"""Magic-byte guard: real signature wins over declared extension."""

from __future__ import annotations

from packs.dms.security.filetype_guard import sniff, validate, EXECUTABLE_TYPES

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"x" * 32
EXE = b"MZ\x90\x00" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16


def test_sniff_known_types() -> None:
    assert sniff(PNG) == "png"
    assert sniff(JPEG) == "jpeg"
    assert sniff(PDF) == "pdf"
    assert sniff(EXE) == "pe"
    assert sniff(WEBP) == "webp"
    assert sniff(b"not a real file") is None


def test_allowed_type_passes() -> None:
    r = validate(PNG, allowed={"png", "jpeg"}, declared_ext="png")
    assert r.ok and r.detected == "png"


def test_extension_spoof_blocked() -> None:
    # PDF bytes wearing a .png name — classic polyglot upload
    r = validate(PDF, allowed={"png", "jpeg", "pdf"}, declared_ext="png")
    assert not r.ok and r.reason and r.reason.startswith("extension_mismatch")


def test_executable_blocked_even_if_allowed_set_is_broad() -> None:
    r = validate(EXE, allowed={"pe", "png"}, declared_ext="png")
    assert not r.ok and r.detected == "pe"
    assert "pe" in EXECUTABLE_TYPES


def test_disallowed_type_blocked() -> None:
    r = validate(PDF, allowed={"png", "jpeg"})
    assert not r.ok and r.reason == "type_not_allowed:pdf"


def test_empty_and_unknown() -> None:
    assert not validate(b"", allowed={"png"}).ok
    assert validate(b"garbagebytes", allowed={"png"}).reason == "unrecognized_signature"
