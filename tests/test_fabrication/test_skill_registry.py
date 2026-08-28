from pathlib import Path
from netie.fabrication.skill_registry import load_skill_cards, SkillRegistry

def test_load_skill_cards():
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    
    cards = load_skill_cards(skills_dir)
    # Count tracks the skills/ directory, which grows as skills are promoted —
    # assert on the contract (every card loads, none is the schema stub) instead.
    assert len(cards) == len([p for p in skills_dir.glob("*.yaml") if p.stem != "_schema"])

    registry = SkillRegistry(cards)

    assert registry.count() == len(cards)

    web_search = registry.get("web_search_v1")
    assert web_search is not None
    assert web_search.name == "Web Search"
    
    # Ensure invalid/schema files are skipped properly without crashing
    assert registry.get("_schema") is None
