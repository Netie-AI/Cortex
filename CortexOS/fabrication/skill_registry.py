import logging
from pathlib import Path
import yaml
from pydantic import BaseModel, ValidationError
from .dsl_parser import InferenceTier

logger = logging.getLogger(__name__)

class SkillCard(BaseModel):
    skill_id: str                    # slug e.g. "web_search_v1"
    name: str
    description: str
    category: str
    example_intents: list[str]       # used for dense retrieval training
    required_tools: list[str]
    required_network: list[str] = []
    annotations: dict = {}
    max_tier: InferenceTier = InferenceTier.TIER2
    version: str = "1.0.0"
    author: str = "community"

    # Performance telemetry (updated by Sovereign Skill Library)
    mean_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    mean_token_cost: float = 0.0
    acceptance_rate: float = 1.0
    weight: float = 1.0             # RLHF-updated retrieval weight

def load_skill_cards(directory: Path) -> list[SkillCard]:
    cards = []
    if not directory.exists() or not directory.is_dir():
        return cards
        
    for file_path in directory.glob("*.yaml"):
        if file_path.name.startswith("_"):
            continue
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data is None:
                    continue
                card = SkillCard(**data)
                cards.append(card)
        except ValidationError as e:
            logger.warning(f"Validation error in skill card {file_path.name}: {e}")
        except yaml.YAMLError as e:
            logger.warning(f"YAML parsing error in skill card {file_path.name}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error loading skill card {file_path.name}: {e}")
            
    return cards

class SkillRegistry:
    def __init__(self, cards: list[SkillCard]):
        self._cards = {card.skill_id: card for card in cards}
        
    def get(self, skill_id: str) -> SkillCard | None:
        return self._cards.get(skill_id)
        
    def all(self) -> list[SkillCard]:
        return list(self._cards.values())
        
    def count(self) -> int:
        return len(self._cards)
