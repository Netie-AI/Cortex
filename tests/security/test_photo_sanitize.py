"""EXIF GPS sanitization tests using Pillow-only fixtures."""

from __future__ import annotations

import io

from PIL import Image

from packs.dms.security.photo_sanitize import has_gps_exif, strip_exif_gps


def _jpeg_bytes(*, with_gps: bool) -> bytes:
    image = Image.new("RGB", (24, 24), color=(70, 80, 90))
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    if with_gps:
        exif[0x8825] = {1: "N", 2: (3.0, 0.0, 0.0)}

    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def test_strip_exif_gps_removes_detectable_gps_and_keeps_valid_jpeg():
    raw = _jpeg_bytes(with_gps=True)
    assert has_gps_exif(raw)

    clean = strip_exif_gps(raw)

    assert not has_gps_exif(clean)
    with Image.open(io.BytesIO(clean)) as image:
        assert image.format == "JPEG"
        image.verify()


def test_non_gps_jpeg_is_not_rejected_by_sanitizer():
    raw = _jpeg_bytes(with_gps=False)
    assert not has_gps_exif(raw)

    clean = strip_exif_gps(raw)

    assert not has_gps_exif(clean)
    with Image.open(io.BytesIO(clean)) as image:
        assert image.format == "JPEG"
        image.verify()
