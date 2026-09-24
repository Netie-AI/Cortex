"""V0 warehouse smoke tests."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "packs" / "dms" / "sql" / "001_warehouse_v0.sql"


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def _jpeg_b64() -> str:
    img = Image.new("RGB", (32, 32), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _jpeg_with_gps_b64() -> str:
    """JPEG carrying a GPS IFD (0x8825), built with Pillow alone.

    Pillow is a hard dependency, so this runs everywhere; the former piexif
    helper made the only F7 GPS-strip test skip on every install (TRUST-06).
    """
    img = Image.new("RGB", (32, 32), color=(40, 50, 60))
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    gps = exif.get_ifd(0x8825)  # GPSInfo IFD
    gps[1] = "N"  # GPSLatitudeRef
    gps[2] = (3.0, 0.0, 0.0)  # GPSLatitude
    gps[3] = "E"  # GPSLongitudeRef
    gps[4] = (101.0, 0.0, 0.0)  # GPSLongitude
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_location_tree_and_qr(ops_db):
    from packs.dms.vision import locations

    zone = locations.build_location(kind="zone", code="Z-A", db_path=ops_db)
    rack = locations.build_location(
        kind="rack", code="R-A1", parent_code="Z-A", db_path=ops_db
    )
    bin_loc = locations.build_location(
        kind="bin", code="B-A1-01", parent_code="R-A1", capacity_volume=100.0, db_path=ops_db
    )
    assert zone["qr_token"]
    assert bin_loc["qr_token"] != rack["qr_token"]

    png = locations.render_qr_png(bin_loc["qr_token"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    tree = locations.location_tree_with_items(db_path=ops_db)
    assert len(tree) == 1
    assert tree[0]["code"] == "Z-A"
    assert tree[0]["children"][0]["children"][0]["code"] == "B-A1-01"


def test_intake_stores_item_photo_and_ledger(ops_db):
    from packs.dms.audit.ledger import list_entries
    from packs.dms.vision import intake, locations

    locations.build_location(kind="bin", code="BIN-1", db_path=ops_db)
    result = intake.intake_item(
        sku="SKU-001",
        label="Widget A",
        location_code="BIN-1",
        photo_b64=_jpeg_b64(),
        actor="fde",
        db_path=ops_db,
    )
    assert result["item"]["sku"] == "SKU-001"
    assert result["ledger_event"] == "item.intake"

    entries = list_entries(db_path=ops_db, event_type="item.intake")
    assert len(entries) == 1
    assert entries[0].payload["sku"] == "SKU-001"


def test_exif_gps_stripped(ops_db):
    from packs.dms.security.photo_sanitize import has_gps_exif, strip_exif_gps
    from packs.dms.vision import intake, locations
    from packs.dms.vision.warehouse_store import photos_dir

    locations.build_location(kind="bin", code="BIN-EXIF", db_path=ops_db)
    photo_b64 = _jpeg_with_gps_b64()
    raw = base64.b64decode(photo_b64)
    # The fixture must really carry GPS, or the assertions below cannot fail.
    assert has_gps_exif(raw)
    assert not has_gps_exif(strip_exif_gps(raw))

    # Submit the RAW GPS-bearing bytes: intake itself is the choke-point.
    result = intake.intake_item(
        sku="SKU-EXIF",
        label="GPS test",
        location_code="BIN-EXIF",
        photo_b64=photo_b64,
        db_path=ops_db,
    )
    uri = result["item"]["photo_uri"]
    stored = photos_dir(ops_db) / Path(uri).name
    assert stored.is_file()
    persisted = stored.read_bytes()
    assert not has_gps_exif(persisted)
    with Image.open(io.BytesIO(persisted)) as img:
        img.verify()
    with Image.open(io.BytesIO(persisted)) as img:
        assert img.size == (32, 32)


def test_scan_move_updates_and_records(ops_db):
    from packs.dms.audit.ledger import list_entries
    from packs.dms.vision import intake, locations, movement

    locations.build_location(kind="bin", code="BIN-A", db_path=ops_db)
    dest = locations.build_location(kind="bin", code="BIN-B", db_path=ops_db)
    intake.intake_item(
        sku="SKU-MOVE",
        label="Movable",
        location_code="BIN-A",
        photo_b64=_jpeg_b64(),
        db_path=ops_db,
    )

    result = movement.scan_move(
        item_qr_or_id="SKU-MOVE",
        to_location_qr=dest["qr_token"],
        actor="operator",
        db_path=ops_db,
    )
    assert result["to_location_code"] == "BIN-B"
    assert result["ledger_event"] == "item.moved"

    moved = list_entries(db_path=ops_db, event_type="item.moved")
    assert len(moved) == 1
    assert moved[0].payload["to_location_code"] == "BIN-B"


def test_rls_on_warehouse_tables(ops_db):
    from packs.dms.vision import locations
    from packs.dms.vision.warehouse_store import (
        RLSViolationError,
        create_item,
        get_location_by_code,
    )

    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "dms_locations_tenant_isolation" in sql
    assert "dms_items_tenant_isolation" in sql
    assert "dms_movements_tenant_isolation" in sql

    locations.build_location(kind="bin", code="T1-BIN", tenant_id="tenant_a", db_path=ops_db)
    loc = get_location_by_code("T1-BIN", tenant_id="tenant_a", db_path=ops_db)
    assert loc is not None

    with pytest.raises(RLSViolationError):
        create_item(
            sku="X",
            label="cross",
            location_id=loc.id,
            photo_uri=None,
            tenant_id="tenant_b",
            db_path=ops_db,
        )


def test_qr_label_endpoint(ops_db):
    pytest.importorskip("fastapi")
    import netie.config
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from packs.dms.vision import locations

    netie.config._cached_config = None
    bin_loc = locations.build_location(kind="bin", code="QR-BIN", db_path=ops_db)

    import os

    os.environ["PACK"] = "dms"
    netie.config._cached_config = None
    app = create_app()
    client = TestClient(app)
    res = client.get(f"/dms/warehouse/locations/{bin_loc['id']}/qr-label")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
