"""Strip EXIF GPS from intake photos (F7 choke-point)."""

from __future__ import annotations

import io

from PIL import Image


def strip_exif_gps(image_bytes: bytes) -> bytes:
    """Return image bytes with EXIF metadata removed (including GPS)."""
    img = Image.open(io.BytesIO(image_bytes))
    fmt = img.format or "JPEG"
    if fmt.upper() == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    save_kwargs: dict = {}
    if fmt.upper() in ("JPEG", "JPG"):
        save_kwargs["quality"] = 90
    img.save(out, format=fmt, **save_kwargs)
    return out.getvalue()


def has_gps_exif(image_bytes: bytes) -> bool:
    """True if image contains GPS EXIF tags."""
    img = Image.open(io.BytesIO(image_bytes))
    exif = img.getexif()
    if not exif:
        return False
    # GPS IFD tag
    return 34853 in exif or any(
        tag in exif for tag in (0x8825,)  # GPSInfo
    )
