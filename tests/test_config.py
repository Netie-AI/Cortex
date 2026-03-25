import os
from pathlib import Path
from netie.config import NettieConfig, load_config, save_config, get_config_path
import netie.config

def test_config_roundtrip(tmp_path, monkeypatch):
    # Mock home path to tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    # Test file doesn't exist
    config = load_config()
    assert config.api_key is None
    
    # Test env var override
    monkeypatch.setenv("NETIE_API_KEY", "test_key")
    netie.config._cached_config = None
    config2 = load_config()
    assert config2.api_key == "test_key"
    
    # Test save
    config3 = NettieConfig(api_key="disk_key", provider="anthropic")
    save_config(config3)
    assert get_config_path().exists()
    
    # Clear cache and reload
    netie.config._cached_config = None
    monkeypatch.delenv("NETIE_API_KEY", raising=False)
    config4 = load_config()
    assert config4.api_key == "disk_key"
    assert config4.provider == "anthropic"
