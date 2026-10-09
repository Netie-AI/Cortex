"""HX-02 supply chain gate (R13.1-R13.3).

The images used to run a bare ``pip install`` against floors with no upper pin,
and ``uv.lock`` recorded ``netie`` 0.1.0 against 2.5.0 and ``litellm>=1`` against
``>=1.84.0``. ``scripts/check_supply_chain.py`` is what now fails the build on
that. It also compares every package an image lock and ``uv.lock`` both
contain, at the image target. Any mismatch fails; there is no allow-list.
These tests run it on the real tree (green) and on planted copies of the tree
(each must go red), so a checker stuck at "OK" cannot pass here. The skew
plants also pass with that one check removed, so the failure is the agreement
guard and not some other check.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from scripts import check_supply_chain as sc
from scripts import lock_images
from tests.invariants.test_lock_skew_baseline import skew_lines_outside_baseline

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


def _plant_image_pin(tree: Path, package: str, version: str, variant: str = "core") -> None:
    lock = tree / "requirements" / f"image-{variant}.lock.txt"
    text = lock.read_text(encoding="utf-8")
    new = re.sub(rf"^{package}==\S+", f"{package}=={version}", text, count=1, flags=re.M)
    assert new != text, f"{package}=={version} did not change {lock.name}"
    lock.write_text(new, encoding="utf-8")


def test_image_uv_version_skew_fails(tree: Path) -> None:
    """A litellm pin that is not the uv.lock version fails outright."""
    _plant_image_pin(tree, "litellm", "1.84.0")
    problems = sc.run(tree)
    assert any(
        "image-core.lock.txt: litellm==1.84.0 disagrees with uv.lock" in p
        for p in problems
    )


def test_unlisted_boto3_pin_fails(tree: Path) -> None:
    """A boto3 pin that is not uv.lock 1.43.108 fails outright."""
    _plant_image_pin(tree, "boto3", "1.43.109")
    problems = sc.run(tree)
    assert any(
        "image-core.lock.txt: boto3==1.43.109 disagrees with uv.lock boto3==1.43.108" in p
        for p in problems
    )


def _set_python_version(tree: Path, name: str, version: str) -> None:
    _edit(tree / name, "ARG PYTHON_VERSION=3.11", f"ARG PYTHON_VERSION={version}")


def test_dockerfile_python_versions_disagree(tree: Path) -> None:
    """One image at 3.12 while the other three stay at 3.11 is a named error."""
    _set_python_version(tree, "Dockerfile.full", "3.12")
    problems = sc.run(tree)
    assert problems == [
        "image Python version disagrees: "
        "Dockerfile=3.11, Dockerfile.constructor=3.11, Dockerfile.core=3.11, Dockerfile.full=3.12"
    ]


def test_dockerfile_python_version_has_no_uv_resolution_entry(tree: Path) -> None:
    """3.99 has no resolution entry. ``>= '3.15'`` names 3.15, not 3.99."""
    for name in sc.DOCKERFILES:
        _set_python_version(tree, name, "3.99")
    problems = sc.run(tree)
    assert problems == ["uv.lock has no resolution entry for Python 3.99"]


def test_skew_line_does_not_hide_a_mismatch(tree: Path) -> None:
    """A lock_skew line does not excuse a shared-package mismatch.

    The allow-list is gone, so ``scripts/check_supply_chain.py`` still fails.
    The frozen baseline in ``tests/invariants/test_lock_skew_baseline.py``
    names the added line, which is outside the ceiling.
    """
    _plant_image_pin(tree, "boto3", "1.43.109", variant="core")
    line = "image-core.lock.txt: boto3==1.43.109 disagrees with uv.lock boto3==1.43.108"
    path = tree / "requirements" / "lock_skew" / "image-core.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line + "\n", encoding="utf-8")
    assert line in sc.mismatch_lines(tree)
    assert any(line in problem for problem in sc.run(tree))
    assert skew_lines_outside_baseline(tree) == [
        f"new skew line not in frozen baseline: image-core: {line}"
    ]


def test_stray_skew_line_is_not_a_supply_chain_failure(tree: Path) -> None:
    """The checker does not read lock_skew. A stale line is not its failure."""
    path = tree / "requirements" / "lock_skew" / "image-core.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "image-core.lock.txt: boto3==1.43.108 disagrees with uv.lock boto3==1.43.110\n",
        encoding="utf-8",
    )
    assert sc.run(tree) == []


def test_shared_pins_match_uv_lock() -> None:
    """The committed image locks and uv.lock agree on every shared package."""
    assert sc.mismatch_lines(ROOT) == []


def test_align_uv_lock_flag_selects_constraints_not_a_blanket_upgrade(tmp_path: Path) -> None:
    variants, upgrades, align = lock_images.parse_args(
        ["--align-uv-lock", "core", "--upgrade-package", "litellm"]
    )
    assert variants == ["core"]
    assert upgrades == ("litellm",)
    assert align is True
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("boto3==1.43.108\n", encoding="utf-8")
    cmd = lock_images.compile_command("core", ("boto3",), constraints)
    assert "--constraints" in cmd
    assert "--overrides" not in cmd
    assert str(constraints) in cmd
    assert "--upgrade" not in cmd
    assert cmd[cmd.index("--upgrade-package") + 1] == "boto3"


def _write_min_tree(tmp_path: Path, uv_packages: str, image_pin: str) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "netie"\nversion = "2.5.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    for name in sc.DOCKERFILES:
        (tmp_path / name).write_text("ARG PYTHON_VERSION=3.11\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        "version = 1\n"
        "resolution-markers = [\n"
        '    "python_full_version == \'3.11.*\'",\n'
        "]\n\n"
        '[[package]]\nname = "netie"\nversion = "2.5.0"\n\n' + uv_packages,
        encoding="utf-8",
    )
    req = tmp_path / "requirements"
    req.mkdir(exist_ok=True)
    digest = "ab" * 32
    (req / "image-core.lock.txt").write_text(
        f"{image_pin} \\\n    --hash=sha256:{digest}\n",
        encoding="utf-8",
    )


def test_ambiguous_uv_resolution_is_a_mismatch_never_a_match(tmp_path: Path) -> None:
    """Two versions that both select the image target do not match either pin."""
    _write_min_tree(
        tmp_path,
        '[[package]]\nname = "boto3"\nversion = "1.43.108"\n'
        'resolution-markers = ["python_full_version == \'3.11.*\'"]\n\n'
        '[[package]]\nname = "boto3"\nversion = "1.43.110"\n'
        'resolution-markers = ["python_full_version == \'3.11.*\'"]\n',
        "boto3==1.43.110",
    )
    lines = sc.mismatch_lines(tmp_path)
    assert len(lines) == 1
    assert "has no single uv.lock version" in lines[0]
    assert "1.43.108" in lines[0] and "1.43.110" in lines[0]
    assert "disagrees with uv.lock boto3==1.43.110" not in lines[0]


def test_image_target_selects_one_uv_version(tmp_path: Path) -> None:
    """Markers narrow a multi-version package to the image's Python."""
    _write_min_tree(
        tmp_path,
        '[[package]]\nname = "boto3"\nversion = "1.43.108"\n'
        'resolution-markers = ["python_full_version < \'3.11\'"]\n\n'
        '[[package]]\nname = "boto3"\nversion = "1.43.110"\n'
        'resolution-markers = ["python_full_version == \'3.11.*\'"]\n',
        "boto3==1.43.110",
    )
    assert sc.mismatch_lines(tmp_path) == []
    _write_min_tree(
        tmp_path,
        '[[package]]\nname = "boto3"\nversion = "1.43.108"\n'
        'resolution-markers = ["python_full_version < \'3.11\'"]\n\n'
        '[[package]]\nname = "boto3"\nversion = "1.43.110"\n'
        'resolution-markers = ["python_full_version == \'3.11.*\'"]\n',
        "boto3==1.43.108",
    )
    assert sc.mismatch_lines(tmp_path) == [
        "image-core.lock.txt: boto3==1.43.108 disagrees with uv.lock boto3==1.43.110"
    ]


def test_packages_in_only_one_lock_are_not_compared(tmp_path: Path) -> None:
    _write_min_tree(
        tmp_path,
        '[[package]]\nname = "only-uv"\nversion = "1.0.0"\n',
        "only-image==9.9.9",
    )
    assert sc.mismatch_lines(tmp_path) == []


def test_image_uv_version_skew_passes_when_agreement_guard_removed(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same plant as the litellm skew. With that check gone, the tree is clean.

    The other checks still see an exact, hashed, in-specifier, non-denylisted
    pin, so a pass here means the failure belongs to ``check_version_agreement``.
    """
    _plant_image_pin(tree, "litellm", "1.84.0")
    monkeypatch.setattr(sc, "check_version_agreement", lambda _root: [])
    assert sc.run(tree) == []
