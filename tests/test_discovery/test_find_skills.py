"""Discovery unit tests — Find Skills ranking + SkillOpt seed."""

from __future__ import annotations

from pathlib import Path

from CortexOS.discovery.find import find_mcp, find_skills, find_subagents, discover_for_goal
from CortexOS.discovery.catalog import load_sources, catalog_snapshot, clear_catalog_cache


def setup_function():
    clear_catalog_cache()


def test_sources_include_awesome_lists():
    sources = load_sources()
    ids = {s["id"] for s in sources["sources"]}
    assert "punkpeye/awesome-mcp-servers" in ids
    assert "BehiSecc/awesome-claude-skills" in ids
    assert "rohitg00/awesome-claude-code-toolkit" in ids
    assert "itgoyo/awesome-agent-skills" in ids
    assert "microsoft/SkillOpt" in ids


def test_catalog_has_hundreds_of_refs():
    items = catalog_snapshot(include_local=True)
    assert len(items) >= 100
    kinds = {i.kind for i in items}
    assert "skill" in kinds or "local_skill" in kinds
    assert "mcp" in kinds


def test_find_skills_playwright_goal():
    res = find_skills("playwright e2e testing reliability", top_k=5)
    assert res["ok"] is True
    assert "Are there any good skills for" in res["question"]
    assert res["best"] is not None
    assert len(res["matches"]) >= 1
    # Prefer skills over MCP for a skill-shaped goal
    assert res["best"]["kind"] in ("skill", "local_skill")
    blob = (res["best"]["name"] + " " + res["best"]["description"] + " " + " ".join(res["best"].get("tags") or [])).lower()
    assert "playwright" in blob or "test" in blob


def test_find_skills_implies_find_skills_meta():
    res = find_skills("find skills meta install discover packages", top_k=8)
    names = " ".join(m["name"].lower() for m in res["matches"])
    assert "find-skills" in names or "find skills" in names.lower() or any(
        "find-skills" in (m.get("id") or "") for m in res["matches"]
    )


def test_find_mcp_github():
    res = find_mcp("github repositories issues pull requests", top_k=5)
    assert res["ok"] is True
    assert res["matches"]
    assert all(m["kind"] == "mcp" for m in res["matches"])


def test_find_subagents():
    res = find_subagents("code review agent plugin", top_k=5)
    assert res["ok"] is True
    # May be empty on sparse catalogs but should not error
    assert "matches" in res


def test_skillopt_evolve_seeds_artifact(tmp_path: Path):
    res = discover_for_goal(
        "playwright testing",
        kinds={"skill", "local_skill"},
        top_k=3,
        evolve=True,
        out_dir=tmp_path,
    )
    assert res.ok
    assert res.best is not None
    meta = res.skillopt
    assert meta.get("ok") is True
    best_path = Path(meta["best_skill_path"])
    assert best_path.exists()
    text = best_path.read_text(encoding="utf-8")
    assert "playwright" in text.lower() or "Goal:" in text


def test_empty_goal_fails_soft():
    res = find_skills("   ")
    assert res["ok"] is False
