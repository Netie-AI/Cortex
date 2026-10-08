"""HX-02 supply chain gate (R13.1-R13.3).

The images used to run a bare ``pip install`` against floors with no upper pin,
and ``uv.lock`` recorded ``netie`` 0.1.0 against 2.5.0 and ``litellm>=1`` against
``>=1.84.0``. ``scripts/check_supply_chain.py`` is what now fails the build on
that. These tests run it on the real tree (green) and on planted copies of the
tree (each must go red), so a checker stuck at "OK" cannot pass here.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from scripts import check_supply_chain as sc
from scripts import lock_images

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A copy of just the files the checker reads."""
    for name in ("pyproject.toml", "uv.lock", *sc.DOCKERFILES):
        shutil.copy(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / "requirements", tmp_path / "requirements")
    assert sc.run(tmp_path) == []
    return tmp_path


def _edit(path: Path, old: str, new: str, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"plant anchor {old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def test_real_tree_passes() -> None:
    assert sc.run(ROOT) == []
    assert sc.main() == 0


def test_variant_tables_agree() -> None:
    """The generator and the checker must describe the same images."""
    assert sc.VARIANTS == lock_images.VARIANTS
    assert set(sc.DOCKERFILES.values()) == set(sc.VARIANTS)


def test_every_image_installs_locked_then_project_no_deps() -> None:
    for name, variant in sc.DOCKERFILES.items():
        installs = sc._pip_installs((ROOT / name).read_text(encoding="utf-8"))
        assert len(installs) == 2, (name, installs)
        assert "--require-hashes" in installs[0]
        assert f"requirements/image-{variant}.lock.txt" in installs[0]
        assert "--no-deps" in installs[1].split()
        assert "--no-build-isolation" in installs[1].split()


def test_locks_pin_litellm_exactly_and_hash_it() -> None:
    for variant in sc.VARIANTS:
        pins, problems = sc.parse_lock(ROOT / "requirements" / f"image-{variant}.lock.txt")
        assert problems == []
        assert "litellm" in pins
        assert ("litellm", pins["litellm"]) not in sc.DENYLIST


# -- plants: each must fail -------------------------------------------------


def test_stale_project_version_in_uv_lock_fails(tree: Path) -> None:
    _edit(tree / "uv.lock", 'name = "netie"\nversion = "2.5.0"', 'name = "netie"\nversion = "0.1.0"')
    assert any("netie is '0.1.0'" in p for p in sc.run(tree))


def test_stale_specifier_in_uv_lock_fails(tree: Path) -> None:
    _edit(tree / "uv.lock", '{ name = "litellm", specifier = ">=1.84.0" }', '{ name = "litellm", specifier = ">=1" }')
    problems = sc.run(tree)
    assert any("requires litellm>=1.84.0 but uv.lock does not" in p for p in problems)
    assert any("records litellm>=1 " in p for p in problems)


def test_pyproject_floor_raised_without_relock_fails(tree: Path) -> None:
    _edit(tree / "pyproject.toml", '"litellm>=1.84.0"', '"litellm>=99"')
    problems = sc.run(tree)
    assert any("uv.lock" in p and "litellm>=99" in p for p in problems)
    for variant in sc.VARIANTS:
        assert any(f"image-{variant}.lock.txt: stale" in p and "litellm" in p for p in problems)


def test_dependency_added_without_relock_fails(tree: Path) -> None:
    _edit(tree / "pyproject.toml", '"httpx>=0.27",', '"httpx>=0.27",\n    "left-pad-never-locked>=1",')
    problems = sc.run(tree)
    for variant in sc.VARIANTS:
        assert any(
            f"image-{variant}.lock.txt: stale" in p and "left-pad-never-locked" in p for p in problems
        )


def test_missing_hash_fails(tree: Path) -> None:
    lock = tree / "requirements" / "image-core.lock.txt"
    text = lock.read_text(encoding="utf-8")
    stripped = re.sub(r"(litellm==[^\s]+) \\\n(?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n)+", r"\1\n", text)
    assert stripped != text
    lock.write_text(stripped, encoding="utf-8")
    assert any("image-core.lock.txt: litellm==" in p and "no --hash" in p for p in sc.run(tree))


def test_unpinned_requirement_fails(tree: Path) -> None:
    lock = tree / "requirements" / "image-full.lock.txt"
    lock.write_text(lock.read_text(encoding="utf-8") + "requests>=2\n", encoding="utf-8")
    assert any("image-full.lock.txt: not an exact pin" in p for p in sc.run(tree))


@pytest.mark.parametrize("bad", ["1.82.7", "1.82.8"])
def test_denylisted_litellm_in_image_lock_fails(tree: Path, bad: str) -> None:
    lock = tree / "requirements" / "image-constructor.lock.txt"
    text = lock.read_text(encoding="utf-8")
    lock.write_text(re.sub(r"^litellm==\S+", f"litellm=={bad}", text, flags=re.M), encoding="utf-8")
    assert any(f"denylisted litellm=={bad}" in p for p in sc.run(tree))


def test_denylisted_litellm_in_uv_lock_fails(tree: Path) -> None:
    text = (tree / "uv.lock").read_text(encoding="utf-8")
    new = re.sub(r'(name = "litellm"\nversion = )"[^"]+"', r'\1"1.82.8"', text)
    assert new != text
    (tree / "uv.lock").write_text(new, encoding="utf-8")
    assert any("uv.lock: denylisted litellm==1.82.8" in p for p in sc.run(tree))


def test_bare_pip_install_in_dockerfile_fails(tree: Path) -> None:
    """The base's install lines, planted back."""
    df = tree / "Dockerfile.core"
    text = df.read_text(encoding="utf-8")
    start = text.index("RUN pip install")
    end = text.index("\n\n", start)
    df.write_text(
        text[:start]
        + "RUN pip install --no-cache-dir --upgrade pip \\\n    && pip install --no-cache-dir ."
        + text[end:],
        encoding="utf-8",
    )
    problems = sc.run(tree)
    assert any("Dockerfile.core: unhashed install" in p and "--upgrade pip" in p for p in problems)
    assert any("Dockerfile.core: never installs" in p for p in problems)


def test_dockerfile_pointing_at_wrong_lock_fails(tree: Path) -> None:
    _edit(tree / "Dockerfile.full", "-r requirements/image-full.lock.txt", "-r requirements/image-core.lock.txt")
    assert any("Dockerfile.full: never installs requirements/image-full.lock.txt" in p for p in sc.run(tree))


def test_missing_image_lock_fails(tree: Path) -> None:
    (tree / "requirements" / "image-full.lock.txt").unlink()
    assert any("image-full.lock.txt: missing" in p for p in sc.run(tree))
