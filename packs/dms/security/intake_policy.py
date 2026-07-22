"""C-SEC-4 — intake file-type policy: filetype_guard in front of every byte sink.

One place decides what an intake path accepts, so the rules can't drift between
routes. Composes ``filetype_guard`` (additive; the guard itself is now wired and
promoted to frozen — extend HERE, not there).

Policies:
  * ``check_photo``   — real png/jpeg/webp only; executables and spoofs denied.
  * ``check_upload``  — for the ingest drop (csv/tsv/json/jsonl/ndjson/xlsx):
      - text extensions must NOT carry a recognizable binary signature — a
        ``report.csv`` that begins with ``MZ`` (a Windows exe) is denied even
        though CSV itself has no magic bytes (fail-closed against masquerade);
      - ``xlsx`` must genuinely be a zip container (OOXML) with matching ext.
"""

from __future__ import annotations

from packs.dms.security.filetype_guard import (
    EXECUTABLE_TYPES,
    GuardResult,
    sniff,
    validate,
)

PHOTO_ALLOWED = frozenset({"png", "jpeg", "webp"})
TEXT_EXTS = frozenset({"csv", "tsv", "json", "jsonl", "ndjson"})
CONTAINER_EXTS = frozenset({"xlsx"})


def check_photo(data: bytes) -> GuardResult:
    """Photo intake: must sniff as an allowed raster image."""
    return validate(data, allowed=set(PHOTO_ALLOWED), declared_ext=None)


def check_upload(data: bytes, ext: str) -> GuardResult:
    """Ingest-drop policy for a declared extension (without the dot)."""
    ext = (ext or "").lower().lstrip(".")
    if not data:
        return GuardResult(False, None, "empty")
    if ext in CONTAINER_EXTS:
        return validate(data, allowed={"zip"}, declared_ext=ext)
    if ext in TEXT_EXTS:
        detected = sniff(data)
        if detected is None:
            return GuardResult(True, None, None)          # plain text — expected
        if detected in EXECUTABLE_TYPES:
            return GuardResult(False, detected, f"executable_blocked:{detected}")
        return GuardResult(False, detected, f"binary_masquerading_as_text:{detected}")
    return GuardResult(False, sniff(data), f"extension_not_allowed:{ext}")
