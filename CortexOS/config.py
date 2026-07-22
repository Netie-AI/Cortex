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
    # Phase 1 RAG / Phase 0 DB
    database_url: str | None = Field(default=None)
    qdrant_url: str | None = Field(default=None)
    qdrant_collection_listings: str = Field(default="listings_dense_v1")
    embedder_model: str = Field(default="BAAI/bge-m3")
    # Vertical pack (Cortex OS)
    pack: str = Field(default="ruma", description="Active vertical pack: ruma | dms")
    pack_dir: str = Field(default="packs", description="Root directory for vertical packs")

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
    if os.environ.get("NETIE_DATABASE_URL"):
        data["database_url"] = os.environ["NETIE_DATABASE_URL"]
    if os.environ.get("NETIE_QDRANT_URL"):
        data["qdrant_url"] = os.environ["NETIE_QDRANT_URL"]
    if os.environ.get("NETIE_QDRANT_COLLECTION"):
        data["qdrant_collection_listings"] = os.environ["NETIE_QDRANT_COLLECTION"]
    if os.environ.get("NETIE_EMBEDDER_MODEL"):
        data["embedder_model"] = os.environ["NETIE_EMBEDDER_MODEL"]
    if os.environ.get("EMBEDDER_MODEL"):
        data["embedder_model"] = os.environ["EMBEDDER_MODEL"]
    if os.environ.get("PACK"):
        data["pack"] = os.environ["PACK"]
    if os.environ.get("NETIE_PACK"):
        data["pack"] = os.environ["NETIE_PACK"]
    if os.environ.get("NETIE_PACK_DIR"):
        data["pack_dir"] = os.environ["NETIE_PACK_DIR"]
        
    _cached_config = NettieConfig(**data)
    return _cached_config

def save_config(config: NettieConfig) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        toml.dump(config.model_dump(exclude_none=True, mode="json"), f)
    
    global _cached_config
    _cached_config = config

def get_config() -> NettieConfig:
    return load_config()
