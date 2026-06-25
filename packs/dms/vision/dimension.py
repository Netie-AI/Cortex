"""Vision-assisted dimension estimation (V1). Suggestions only — never auto-commit."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Any

from PIL import Image

VISION_MODEL = os.environ.get("VISION_MODEL", "VISION_MODEL")
DEPTH_SOURCE = os.environ.get("DEPTH_SOURCE", "reference_marker")

_BLOCKED_MODEL_KINDS = frozenset({"generation", "image_generation", "edit", "inpaint"})
_ALLOWED_MODEL_KINDS = frozenset({"detection", "understanding", "depth"})

REFERENCE_MARKER_SIZE_M = 0.10


@dataclass(frozen=True, slots=True)
class DimensionSuggestion:
    l: float
    w: float
    h: float
    unit: str
    confidence: float
    depth_source: str
    vision_model: str
    status: str = "suggestion"

    def to_dict(self) -> dict[str, Any]:
        return {
            "l": self.l,
            "w": self.w,
            "h": self.h,
            "unit": self.unit,
            "confidence": self.confidence,
            "depth_source": self.depth_source,
            "vision_model": self.vision_model,
            "status": self.status,
        }


def assert_measurement_model(model_kind: str) -> None:
    """Reject generation models in measurement paths (governance test hook)."""
    kind = model_kind.strip().lower()
    if kind in _BLOCKED_MODEL_KINDS:
        raise ValueError(f"generation model cannot be used for measurement: {model_kind!r}")
    if kind not in _ALLOWED_MODEL_KINDS:
        raise ValueError(f"unsupported model kind for measurement: {model_kind!r}")


def item_volume(dims: dict[str, Any]) -> float:
    return float(dims["l"]) * float(dims["w"]) * float(dims["h"])


def estimate_dims(
    photo: bytes,
    *,
    depth_source: str | None = None,
    depth_map: bytes | None = None,
    model_kind: str = "detection",
) -> DimensionSuggestion:
    """Estimate item dimensions from photo. Returns a suggestion — not stored as fact."""
    assert_measurement_model(model_kind)
    source = (depth_source or DEPTH_SOURCE).strip().lower()
    if source == "lidar":
        return _estimate_from_lidar(photo, depth_map)
    if source == "reference_marker":
        return _estimate_from_reference_marker(photo)
    raise ValueError(f"unsupported DEPTH_SOURCE: {source!r}")



def _extend_bbox(
    bbox: tuple[int, int, int, int] | None, x: int, y: int
) -> tuple[int, int, int, int]:
    if bbox is None:
        return (x, y, x, y)
    x0, y0, x1, y1 = bbox
    return (min(x0, x), min(y0, y), max(x1, x), max(y1, y))


def _estimate_from_reference_marker(photo: bytes) -> DimensionSuggestion:
    img = Image.open(io.BytesIO(photo)).convert("RGB")
    w_px, h_px = img.size
    pixels = img.load()

    marker_bbox: tuple[int, int, int, int] | None = None
    item_bbox: tuple[int, int, int, int] | None = None

    for y in range(h_px):
        for x in range(w_px):
            r, g, b = pixels[x, y]
            is_marker = r > 200 and g < 80 and b < 80
            is_item = r < 180 and g < 180 and b < 180 and not is_marker
            if is_marker:
                marker_bbox = _extend_bbox(marker_bbox, x, y)
            elif is_item:
                item_bbox = _extend_bbox(item_bbox, x, y)

    if marker_bbox is None or item_bbox is None:
        scale = REFERENCE_MARKER_SIZE_M / max(w_px * 0.1, 1.0)
        est_l = round(w_px * 0.5 * scale, 3)
        est_w = round(w_px * 0.35 * scale, 3)
        est_h = round(h_px * 0.25 * scale, 3)
        confidence = 0.55
    else:
        m_w = max(marker_bbox[2] - marker_bbox[0] + 1, 1)
        scale = REFERENCE_MARKER_SIZE_M / m_w
        i_w = max(item_bbox[2] - item_bbox[0] + 1, 1)
        i_h = max(item_bbox[3] - item_bbox[1] + 1, 1)
        est_l = round(i_w * scale, 3)
        est_w = round(i_w * 0.6 * scale, 3)
        est_h = round(i_h * scale, 3)
        confidence = 0.82

    return DimensionSuggestion(
        l=est_l,
        w=est_w,
        h=est_h,
        unit="m",
        confidence=confidence,
        depth_source="reference_marker",
        vision_model=VISION_MODEL,
    )


def _estimate_from_lidar(photo: bytes, depth_map: bytes | None) -> DimensionSuggestion:
    if depth_map is None:
        raise ValueError("lidar depth_source requires depth_map")
    img = Image.open(io.BytesIO(photo)).convert("L")
    depth = Image.open(io.BytesIO(depth_map)).convert("L")
    if img.size != depth.size:
        raise ValueError("depth_map dimensions must match photo")

    w_px, h_px = img.size
    pixels = img.load()
    depth_px = depth.load()

    depths: list[float] = []
    xs: list[int] = []
    ys: list[int] = []
    for y in range(h_px):
        for x in range(w_px):
            if pixels[x, y] < 200:
                depths.append(depth_px[x, y] / 255.0)
                xs.append(x)
                ys.append(y)

    if not depths:
        raise ValueError("no foreground pixels detected for lidar path")

    median_depth = sorted(depths)[len(depths) // 2]
    span_x = max(xs) - min(xs) + 1
    span_y = max(ys) - min(ys) + 1
    est_l = round(span_x * median_depth * 0.01, 3)
    est_w = round(span_x * median_depth * 0.006, 3)
    est_h = round(span_y * median_depth * 0.01, 3)

    return DimensionSuggestion(
        l=est_l,
        w=est_w,
        h=est_h,
        unit="m",
        confidence=0.78,
        depth_source="lidar",
        vision_model=VISION_MODEL,
    )
