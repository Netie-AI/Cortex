"""Discovery half of load_skill: does the roster tell the truth about itself?

Every assertion here is on what a caller actually receives - the rendered
roster block that goes into the prompt, the refusal string the model is shown,
and the parse_errors list an operator is expected to surface. An index that
quietly drops a skill or quietly shortens the roster looks exactly like a
skills directory that is missing files, so the truncation note and the error
list are the behaviour under test, not decoration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.crew.skill_index import (
    DESCRIPTION_MAX_CHARS,
    SkillIndexError,
    build_index,
    clear_cache,
    index_for,
    render_roster,
)


def _skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir(parents=True)
    return root


def test_frontmatter_and_leading_line_both_give_a_description(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "outreach.md").write_text(
        "---\nname: outreach\ndescription: First-touch mail. Never auto-send.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (root / "security.md").write_text(
        "# Security: evidence or fail.\n\nCover authn, RBAC, secrets.\n", encoding="utf-8"
    )
    index = build_index(root)
    by_name = {e.name: e for e in index.entries}
    assert by_name["outreach"].description == "First-touch mail. Never auto-send."
    assert by_name["security"].description == "Security: evidence or fail."
    assert index.parse_errors == ()


def test_nested_skill_md_is_indexed_under_its_folder_name(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "ship").mkdir()
    (root / "ship" / "SKILL.md").write_text("Ship gate before merge.\n", encoding="utf-8")
    index = build_index(root)
    assert index.names == ("ship",)
    assert index.entries[0].rel == "ship/SKILL.md"


def test_ordering_is_deterministic_regardless_of_creation_order(tmp_path: Path) -> None:
    first = _skills(tmp_path)
    for name in ("zeta", "alpha", "middle"):
        (first / f"{name}.md").write_text(f"{name} desc\n", encoding="utf-8")
    second = _skills(tmp_path / "other")
    for name in ("middle", "zeta", "alpha"):
        (second / f"{name}.md").write_text(f"{name} desc\n", encoding="utf-8")
    assert build_index(first).names == ("alpha", "middle", "zeta")
    assert build_index(second).names == build_index(first).names


def test_malformed_files_are_skipped_and_named_not_silently_dropped(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "good.md").write_text("Good skill, still usable.\n", encoding="utf-8")
    (root / "unterminated.md").write_text("---\nname: broken\ndescription: x\n", encoding="utf-8")
    (root / "empty.md").write_text("   \n\n", encoding="utf-8")
    (root / "binary.md").write_bytes(b"\xff\xfe\x00not utf8")
    index = build_index(root)
    assert index.names == ("good",)
    reported = " | ".join(index.parse_errors)
    assert "unterminated.md" in reported
    assert "never closed" in reported
    assert "empty.md" in reported
    assert "binary.md" in reported
    # The operator-facing block must admit the skips, or a bad file reads as absent.
    block = index.render()
    assert "3 skill file(s) skipped as unreadable" in block
    assert "unterminated.md" in block


def test_duplicate_names_are_recorded_rather_than_shadowing(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "a.md").write_text("---\nname: decide\ndescription: first\n---\n", encoding="utf-8")
    (root / "b.md").write_text("---\nname: Decide\ndescription: second\n---\n", encoding="utf-8")
    index = build_index(root)
    assert index.names == ("decide",)
    assert index.entries[0].description == "first"
    assert any("duplicate skill name" in e for e in index.parse_errors)


def test_render_lists_titles_with_one_line_descriptions(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "decide.md").write_text("Pick one owner and stop.\n", encoding="utf-8")
    block = build_index(root).render()
    assert "- decide: Pick one owner and stop." in block
    assert "truncated" not in block


def test_render_ceiling_holds_and_says_how_many_it_omitted(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    for i in range(60):
        (root / f"skill-{i:02d}.md").write_text(
            f"Skill {i} does a specific job worth one whole line of roster.\n", encoding="utf-8"
        )
    index = build_index(root)
    full = index.render(limit=100_000)
    assert full.count("\n- ") == 60

    block = index.render(limit=600)
    assert len(block) <= 600
    assert "roster truncated" in block
    shown = block.count("\n- ")
    omitted = 60 - shown
    assert 0 < shown < 60
    assert f"{omitted} more skill(s) omitted" in block


def test_render_under_a_ceiling_too_small_for_one_line_still_says_so(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    for i in range(3):
        (root / f"s{i}.md").write_text(f"Description {i}\n", encoding="utf-8")
    block = build_index(root).render(limit=120)
    assert "\n- " not in block
    assert "3 more skill(s) omitted" in block


def test_empty_root_and_missing_root_render_plainly(tmp_path: Path) -> None:
    assert build_index(_skills(tmp_path)).render() == "Skills: none indexed."
    missing = build_index(tmp_path / "nope")
    assert missing.entries == ()
    assert missing.render() == "Skills: none indexed."


def test_long_description_is_capped_so_one_skill_cannot_own_the_roster(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "verbose.md").write_text("word " * 200, encoding="utf-8")
    entry = build_index(root).entries[0]
    assert len(entry.description) <= DESCRIPTION_MAX_CHARS
    assert entry.description.endswith("...")


def test_resolve_returns_the_body_path_inside_the_root(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "infra.md").write_text("Infra notes.\n", encoding="utf-8")
    index = build_index(root)
    path = index.resolve("INFRA")
    assert path == (root / "infra.md").resolve()
    assert path.read_text(encoding="utf-8").startswith("Infra")


def test_unknown_name_refuses_with_near_matches_not_silence(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    for name in ("security", "seo", "ship"):
        (root / f"{name}.md").write_text(f"{name} desc\n", encoding="utf-8")
    index = build_index(root)
    with pytest.raises(SkillIndexError) as err:
        index.resolve("securty")
    message = str(err.value)
    assert message.startswith("DENIED:")
    assert "securty" in message
    assert "security" in message


def test_unknown_name_with_no_near_match_still_lists_what_exists(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "outreach.md").write_text("Outreach desc\n", encoding="utf-8")
    with pytest.raises(SkillIndexError) as err:
        build_index(root).resolve("quantum")
    assert "Known skills: outreach" in str(err.value)


def test_unknown_name_on_empty_index_says_nothing_is_indexed(tmp_path: Path) -> None:
    with pytest.raises(SkillIndexError) as err:
        build_index(_skills(tmp_path)).resolve("anything")
    assert "No skills are indexed" in str(err.value)


@pytest.mark.parametrize(
    "name",
    ["../secret", "..\\secret", "sub/skill", "/etc/passwd", "C:\\Windows\\win.ini", "..", ""],
)
def test_resolve_never_escapes_the_skills_root(tmp_path: Path, name: str) -> None:
    root = _skills(tmp_path)
    (root / "safe.md").write_text("Safe skill.\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("SECRET OUTSIDE ROOT\n", encoding="utf-8")
    index = build_index(root)
    with pytest.raises(SkillIndexError) as err:
        index.resolve(name)
    assert str(err.value).startswith("DENIED:")


def test_every_indexed_path_stays_under_the_root(tmp_path: Path) -> None:
    root = _skills(tmp_path)
    (root / "a.md").write_text("A\n", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "SKILL.md").write_text("N\n", encoding="utf-8")
    index = build_index(root)
    for entry in index.entries:
        assert entry.path.resolve().is_relative_to(index.root)


def test_index_for_caches_until_the_directory_changes(tmp_path: Path) -> None:
    clear_cache()
    root = _skills(tmp_path)
    (root / "one.md").write_text("First.\n", encoding="utf-8")
    first = index_for(root)
    assert index_for(root) is first
    (root / "two.md").write_text("Second.\n", encoding="utf-8")
    second = index_for(root)
    assert second is not first
    assert second.names == ("one", "two")
    clear_cache()
    assert index_for(root) is not second


def test_render_roster_matches_the_index_render(tmp_path: Path) -> None:
    clear_cache()
    root = _skills(tmp_path)
    (root / "ship.md").write_text("Ship gate before merge.\n", encoding="utf-8")
    assert render_roster(root) == build_index(root).render()


def test_shipped_skill_packs_index_without_a_single_parse_error() -> None:
    """The packs that ship in git are the real corpus; a regression shows here."""
    from CortexOS.crew.board import PACKS_DIR

    index = build_index(PACKS_DIR)
    assert index.parse_errors == ()
    assert "security" in index.names
    assert "skill-route" in index.names
    block = index.render()
    assert len(block) <= 2400
    assert all(e.description for e in index.entries)
