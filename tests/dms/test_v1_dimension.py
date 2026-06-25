"""V1 dimensioning smoke tests."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    return db


def _marker_photo_b64() -> str:
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 170, 30, 190), fill=(220, 20, 20))
    draw.rectangle((60, 60, 140, 120), fill=(80, 80, 80))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _lidar_pair_b64() -> tuple[str, str]:
    photo = Image.new("L", (100, 100), color=255)
    draw = ImageDraw.Draw(photo)
    draw.rectangle((20, 20, 70, 70), fill=40)
    depth = Image.new("L", (100, 100), color=0)
    draw_d = ImageDraw.Draw(depth)
    draw_d.rectangle((20, 20, 70, 70), fill=180)
    pbuf, dbuf = io.BytesIO(), io.BytesIO()
    photo.save(pbuf, format="PNG")
    depth.save(dbuf, format="PNG")
    return (
        base64.b64encode(pbuf.getvalue()).decode("ascii"),
        base64.b64encode(dbuf.getvalue()).decode("ascii"),
    )


def test_estimate_returns_dims_and_confidence(ops_db):
    from packs.dms.vision.dimension import estimate_dims

    photo = base64.b64decode(_marker_photo_b64())
    suggestion = estimate_dims(photo, depth_source="reference_marker")
    assert suggestion.l > 0
    assert suggestion.w > 0
    assert suggestion.h > 0
    assert suggestion.unit == "m"
    assert 0.0 < suggestion.confidence <= 1.0
    assert suggestion.depth_source == "reference_marker"
    assert suggestion.status == "suggestion"

    photo_b64, depth_b64 = _lidar_pair_b64()
    lidar = estimate_dims(
        base64.b64decode(photo_b64),
        depth_source="lidar",
        depth_map=base64.b64decode(depth_b64),
    )
    assert lidar.depth_source == "lidar"
    assert lidar.l > 0


def test_dims_require_confirm_before_fact(ops_db):
    from packs.dms.audit.ledger import list_entries
    from packs.dms.vision import intake, locations
    from packs.dms.vision.warehouse_store import get_item_by_id_or_qr

    locations.build_location(
        kind="bin", code="BIN-DIM", capacity_volume=10.0, db_path=ops_db
    )
    result = intake.intake_item(
        sku="SKU-DIM",
        label="Sized widget",
        location_code="BIN-DIM",
        photo_b64=_marker_photo_b64(),
        db_path=ops_db,
    )
    assert result["suggested_dims"]["status"] == "suggestion"
    item = get_item_by_id_or_qr(result["item"]["id"], db_path=ops_db)
    assert item is not None
    assert item.dims is None

    confirmed = intake.confirm_item_dims(
        item_id=result["item"]["id"],
        l=0.4,
        w=0.3,
        h=0.2,
        db_path=ops_db,
    )
    assert confirmed["item"]["dims"]["l"] == 0.4
    item_after = get_item_by_id_or_qr(result["item"]["id"], db_path=ops_db)
    assert item_after is not None
    assert item_after.dims is not None

    dimensioned = list_entries(db_path=ops_db, event_type="item.dimensioned")
    assert len(dimensioned) == 1


def test_free_space_accounting(ops_db):
    from packs.dms.vision import intake, locations, space

    loc = locations.build_location(
        kind="bin", code="BIN-SPACE", capacity_volume=1.0, db_path=ops_db
    )
    intake_result = intake.intake_item(
        sku="SKU-SPACE",
        label="Volume test",
        location_code="BIN-SPACE",
        photo_b64=_marker_photo_b64(),
        db_path=ops_db,
    )
    intake.confirm_item_dims(
        item_id=intake_result["item"]["id"],
        l=0.5,
        w=0.4,
        h=0.5,
        db_path=ops_db,
    )
    report = space.location_space(loc["id"], db_path=ops_db)
    assert report["capacity_volume"] == 1.0
    assert report["occupied_volume"] == pytest.approx(0.1, rel=1e-3)
    assert report["free_volume"] == pytest.approx(0.9, rel=1e-3)


def test_no_generation_model_in_measurement(ops_db):
    from packs.dms.vision.dimension import assert_measurement_model, estimate_dims
    from packs.dms.vision import intake, locations

    with pytest.raises(ValueError, match="generation model"):
        assert_measurement_model("generation")

    photo = base64.b64decode(_marker_photo_b64())
    with pytest.raises(ValueError, match="generation model"):
        estimate_dims(photo, model_kind="generation")

    locations.build_location(
        kind="bin", code="BIN-GATE", capacity_volume=0.01, db_path=ops_db
    )
    result = intake.intake_item(
        sku="SKU-GATE",
        label="Oversize",
        location_code="BIN-GATE",
        photo_b64=_marker_photo_b64(),
        db_path=ops_db,
    )
    with pytest.raises(ValueError, match="compliance gate"):
        intake.confirm_item_dims(
            item_id=result["item"]["id"],
            l=1.0,
            w=1.0,
            h=1.0,
            db_path=ops_db,
        )
