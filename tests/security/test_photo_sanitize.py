"""TRUST-06: F7 EXIF-GPS choke-point, asserted on the photo intake persists.

The fixtures are built with Pillow alone (a hard dependency), so these tests
never skip. Every assertion reads back the file intake wrote to disk, which is
the artifact that later leaves the device; asserting on an intermediate value
would certify a broken strip as working.

Proven:
  P1  the pre-strip fixture bytes really carry GPS (the check is able to fail)
  P2  a GPS-bearing JPEG submitted through intake is persisted without GPS
      and still decodes as the same image
  P3  a JPEG without GPS is accepted and persisted as a readable image
      (no false-positive refusal)
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

GPS_IFD = 0x8825


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def _jpeg(*, with_gps: bool, size: tuple[int, int] = (48, 32)) -> bytes:
    img = Image.new("RGB", size, color=(200, 40, 40))
    buf = io.BytesIO()
    if with_gps:
        exif = Image.Exif()
        exif[0x010F] = "TestCam"  # Make
        gps = exif.get_ifd(GPS_IFD)
        gps[1] = "N"  # GPSLatitudeRef
        gps[2] = (3.0, 8.0, 0.0)  # GPSLatitude
        gps[3] = "E"  # GPSLongitudeRef
        gps[4] = (101.0, 41.0, 0.0)  # GPSLongitude
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def _intake(ops_db: Path, sku: str, photo: bytes) -> tuple[dict, Path]:
    from packs.dms.vision import intake, locations
    from packs.dms.vision.warehouse_store import photos_dir

    code = f"BIN-{sku}"
    locations.build_location(kind="bin", code=code, db_path=ops_db)
    result = intake.intake_item(
        sku=sku,
        label=f"{sku} label",
        location_code=code,
        photo_b64=base64.b64encode(photo).decode("ascii"),
        actor="fde",
        db_path=ops_db,
    )
    stored = photos_dir(ops_db) / Path(result["item"]["photo_uri"]).name
    return result, stored


def _assert_decodes(data: bytes, size: tuple[int, int]) -> None:
    with Image.open(io.BytesIO(data)) as img:
        img.verify()
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        assert img.size == size
        assert img.format == "JPEG"


def test_prestrip_fixture_reports_gps():
    """P1: without stripping, the detector sees GPS, so P2 can fail."""
    from packs.dms.security.photo_sanitize import has_gps_exif

    raw = _jpeg(with_gps=True)
    assert has_gps_exif(raw) is True
    with Image.open(io.BytesIO(raw)) as img:
        assert img.getexif().get_ifd(GPS_IFD).get(1) == "N"
    assert has_gps_exif(_jpeg(with_gps=False)) is False


def test_intake_persists_photo_without_gps(ops_db):
    """P2: the bytes on disk after intake carry no GPS and still decode."""
    from packs.dms.audit.ledger import list_entries
    from packs.dms.security.photo_sanitize import has_gps_exif

    raw = _jpeg(with_gps=True)
    result, stored = _intake(ops_db, "SKU-GPS", raw)

    assert stored.is_file()
    persisted = stored.read_bytes()
    assert persisted != raw
    assert has_gps_exif(persisted) is False
    with Image.open(io.BytesIO(persisted)) as img:
        assert dict(img.getexif().get_ifd(GPS_IFD)) == {}
    _assert_decodes(persisted, (48, 32))

    # The ledger and item point at that same stored file.
    assert result["item"]["photo_uri"] == f"dms_photos/{stored.name}"
    entries = list_entries(db_path=ops_db, event_type="item.intake")
    assert [e.payload["photo_uri"] for e in entries] == [result["item"]["photo_uri"]]


def test_intake_accepts_photo_without_gps(ops_db):
    """P3: a clean photo is not refused and is persisted readable."""
    from packs.dms.security.photo_sanitize import has_gps_exif

    raw = _jpeg(with_gps=False, size=(40, 24))
    result, stored = _intake(ops_db, "SKU-CLEAN", raw)

    assert result["ledger_event"] == "item.intake"
    assert result["item"]["sku"] == "SKU-CLEAN"
    assert stored.is_file()
    persisted = stored.read_bytes()
    assert has_gps_exif(persisted) is False
    _assert_decodes(persisted, (40, 24))
