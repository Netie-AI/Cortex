"""Shared helpers for YC multiplayer Playwright recorders."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

OV = os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000").rstrip("/")
CX = os.environ.get("CORTEX_URL", "http://127.0.0.1:8000").rstrip("/")
IDE = os.environ.get("OPENIDE_URL", "http://127.0.0.1:8765").rstrip("/")

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("VIDEO_OUT_DIR", str(ROOT / "out")))
OUT.mkdir(parents=True, exist_ok=True)


def launch(p) -> Browser:
    return p.chromium.launch(headless=True)


def new_page(browser: Browser, name: str) -> Page:
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(OUT),
        record_video_size={"width": 1440, "height": 900},
    )
    page = context.new_page()
    page._yc_context = context  # type: ignore[attr-defined]
    page._yc_name = name  # type: ignore[attr-defined]
    return page


def finish(page: Page) -> Path:
    context = page._yc_context  # type: ignore[attr-defined]
    name = page._yc_name  # type: ignore[attr-defined]
    video = page.video
    page.close()
    dest = OUT / f"{name}.webm"
    if video is not None:
        raw = Path(video.path())
        context.close()
        if dest.exists():
            dest.unlink()
        raw.replace(dest)
    else:
        context.close()
    return dest


def pause(page: Page, ms: int = 900) -> None:
    page.wait_for_timeout(ms)
