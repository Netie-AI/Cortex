"""Magic-byte file-type validation — run BEFORE any parser touches bytes.

Defeats polyglot / spoofed uploads (a ``.png`` that is really a PDF or a ZIP
with an executable payload) by validating the real signature and, when a
declared extension is present, flagging extension/content mismatch.

Pure-stdlib, additive. Intended to sit in front of ``photo_sanitize`` and any
file-intake route. It classifies; the caller decides allow/deny via ``validate``.
"""

from __future__ import annotations

from dataclasses import dataclass

# (offset, signature bytes) -> canonical type. Order: most specific first.
_SIGS: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"\xff\xd8\xff", "jpeg"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"%PDF-", "pdf"),
    (0, b"BM", "bmp"),
    (0, b"II*\x00", "tiff"),
    (0, b"MM\x00*", "tiff"),
    (0, b"PK\x03\x04", "zip"),      # also docx/xlsx/pptx/jar/apk (OOXML is zip)
    (0, b"PK\x05\x06", "zip"),      # empty archive
    (0, b"\x1f\x8b", "gzip"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z"),
    (0, b"Rar!\x1a\x07", "rar"),
    (0, b"OggS", "ogg"),
    (0, b"\x00\x00\x01\x00", "ico"),
    (0, b"MZ", "pe"),               # Windows exe/dll — high-risk
    (0, b"\x7fELF", "elf"),         # Linux binary — high-risk
    (0, b"\xca\xfe\xba\xbe", "macho"),
    (0, b"#!", "script"),           # shebang
)

# Extension -> the content types that legitimately back it.
_EXT_EXPECT: dict[str, set[str]] = {
    "png": {"png"}, "jpg": {"jpeg"}, "jpeg": {"jpeg"}, "gif": {"gif"},
    "bmp": {"bmp"}, "tif": {"tiff"}, "tiff": {"tiff"}, "webp": {"webp"},
    "pdf": {"pdf"},
    "zip": {"zip"}, "docx": {"zip"}, "xlsx": {"zip"}, "pptx": {"zip"},
    "gz": {"gzip"}, "7z": {"7z"}, "rar": {"rar"},
}

# Types that should never be accepted through a document/image intake path.
EXECUTABLE_TYPES = frozenset({"pe", "elf", "macho", "script"})


@dataclass(frozen=True, slots=True)
class GuardResult:
    ok: bool
    detected: str | None
    reason: str | None = None


def sniff(data: bytes) -> str | None:
    """Return the canonical type from magic bytes, or None if unrecognized."""
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    for offset, sig, name in _SIGS:
        if data[offset:offset + len(sig)] == sig:
            return name
    return None


def validate(
    data: bytes,
    *,
    allowed: set[str],
    declared_ext: str | None = None,
    block_executables: bool = True,
) -> GuardResult:
    """Validate real content type against an allow-list and the declared extension.

    * ``allowed`` — canonical types the intake path accepts (e.g. {"png","jpeg","pdf"}).
    * ``declared_ext`` — the filename extension, if any (without the dot).
    * Blocks unrecognized bytes, executables, disallowed types, and ext/content spoofs.
    """
    if not data:
        return GuardResult(False, None, "empty")
    detected = sniff(data)
    if detected is None:
        return GuardResult(False, None, "unrecognized_signature")
    if block_executables and detected in EXECUTABLE_TYPES:
        return GuardResult(False, detected, f"executable_blocked:{detected}")
    if detected not in allowed:
        return GuardResult(False, detected, f"type_not_allowed:{detected}")
    if declared_ext:
        ext = declared_ext.lower().lstrip(".")
        expected = _EXT_EXPECT.get(ext)
        if expected and detected not in expected:
            return GuardResult(False, detected, f"extension_mismatch:{ext}!={detected}")
    return GuardResult(True, detected, None)
