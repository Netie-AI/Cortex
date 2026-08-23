"""Hyperlift builds the repo-root Dockerfile. It must stay the auth-on Constructor image."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_hyperlift_dockerfile_matches_constructor() -> None:
    assert (ROOT / "Dockerfile").read_text(encoding="utf-8") == (
        ROOT / "Dockerfile.constructor"
    ).read_text(encoding="utf-8")


def test_constructor_image_keeps_auth_on_and_seeds_warehouse() -> None:
    text = (ROOT / "Dockerfile.constructor").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "DMS_AUTH_DISABLED=1" not in code
    assert "DMS_REFUSE_DEMO_KEYS=1" in code
    assert "COPY data/samples" in text
    assert "load_inventory_csv" in text
    assert "PORT=8080" in text
