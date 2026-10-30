"""Image-only package ceiling (#377).

``scripts/check_supply_chain.py`` fails any package in an image lock that
``uv.lock`` never resolves for the image target, unless it is on
``IMAGE_ONLY_PACKAGES`` with a reason. That list is the one way to ship code
no test environment installed, so it is capped here. It may shrink in any
commit. Growing it means editing this protected file, which needs
``INVARIANT-CHANGE:`` in the commit body and a reviewer who agrees.

Frozen at the two packages ``requirements/build.in`` adds on purpose:
``poetry-core`` (the hashed build backend for the ``--no-deps
--no-build-isolation`` project install) and ``wheel`` (#376, so the image
does not keep the unhashed wheel the slim base ships).
"""

from __future__ import annotations

import pytest

from scripts import check_supply_chain as sc

_IMAGE_ONLY_CEILING: frozenset[str] = frozenset({"poetry-core", "wheel"})


def test_image_only_packages_stay_inside_the_ceiling() -> None:
    extra = set(sc.IMAGE_ONLY_PACKAGES) - _IMAGE_ONLY_CEILING
    assert not extra, (
        f"IMAGE_ONLY_PACKAGES grew by {sorted(extra)}. Put the package in uv.lock "
        "instead, or raise the ceiling here with INVARIANT-CHANGE: and a reason."
    )


def test_ceiling_check_can_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grow the real list and run the real assertion: it must raise."""
    monkeypatch.setattr(
        sc, "IMAGE_ONLY_PACKAGES", {**sc.IMAGE_ONLY_PACKAGES, "evil-extra": "planted"}
    )
    with pytest.raises(AssertionError, match="evil-extra"):
        test_image_only_packages_stay_inside_the_ceiling()
