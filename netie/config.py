import os
from pathlib import Path
from pydantic import BaseModel, Field
import toml

class NettieConfig(BaseModel):
    api_key: str | None = Field(default=None)
    provider: str = Field(default="openai")
    synthesis_model: str = Field(default="gpt-4o-mini")
    tier2_model: str = Field(default="gpt-4o")
    default_local_model: str = Field(default="microsoft/Phi-3-mini-4k-instruct-gguf")

_cached_config: NettieConfig | None = None

def get_config_path() -> Path:
    return Path.home() / ".netie" / "config.toml"

def load_config() -> NettieConfig:
    global _cached_config
    if _cached_config is not None:
        return _cached_config
    
    path = get_config_path()
    data = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = toml.load(f)
            
    # Override with env vars
    if os.environ.get("NETIE_API_KEY"):
        data["api_key"] = os.environ["NETIE_API_KEY"]
    if os.environ.get("NETIE_PROVIDER"):
        data["provider"] = os.environ["NETIE_PROVIDER"]
    if os.environ.get("NETIE_SYNTHESIS_MODEL"):
        data["synthesis_model"] = os.environ["NETIE_SYNTHESIS_MODEL"]
    if os.environ.get("NETIE_TIER2_MODEL"):
        data["tier2_model"] = os.environ["NETIE_TIER2_MODEL"]
    if os.environ.get("NETIE_DEFAULT_LOCAL_MODEL"):
        data["default_local_model"] = os.environ["NETIE_DEFAULT_LOCAL_MODEL"]
        
    _cached_config = NettieConfig(**data)
    return _cached_config

def save_config(config: NettieConfig) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        toml.dump(config.model_dump(exclude_none=True), f)
    
    global _cached_config
    _cached_config = config

def get_config() -> NettieConfig:
    return load_config()
