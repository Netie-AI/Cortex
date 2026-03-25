from pathlib import Path
from netie.fabrication.skill_registry import load_skill_cards, SkillRegistry

def test_load_skill_cards():
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    
    cards = load_skill_cards(skills_dir)
    assert len(cards) == 6
    
    registry = SkillRegistry(cards)
    
    assert registry.count() == 6
    
    web_search = registry.get("web_search_v1")
    assert web_search is not None
    assert web_search.name == "Web Search"
    
    # Ensure invalid/schema files are skipped properly without crashing
    assert registry.get("_schema") is None
