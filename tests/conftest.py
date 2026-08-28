import sys
import types

import pytest


@pytest.fixture(autouse=True)
def reset_config_cache(monkeypatch):
    """Prevent config.toml / env from polluting pack path resolution in tests."""
    monkeypatch.setenv("PACK", "ruma")
    import netie.config

    netie.config._cached_config = None
    yield
    netie.config._cached_config = None


@pytest.fixture(autouse=True)
def mock_sentence_transformer(monkeypatch):
    """
    Mocks sentence_transformers before any importer loads the real stack
    (avoids heavyweight sklearn/scipy imports on constrained CI images).
    """
    class MockTransformer:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, sentences, *args, **kwargs):
            if isinstance(sentences, str):
                return [0.1] * 384
            return [[0.1] * 384 for _ in sentences]

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = MockTransformer
    fake_st.CrossEncoder = MockTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers.models",
        types.ModuleType("sentence_transformers.models"),
    )

    # skillmesh imports SentenceTransformer lazily (packaging profiles: the rag
    # extra may be absent), so the module-level symbol only exists on builds that
    # still bind it at import time. Where it is absent the sys.modules fake above
    # already covers the deferred import.
    for mod_name in ("netie.fabrication.skillmesh", "CortexOS.fabrication.skillmesh"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "SentenceTransformer"):
            monkeypatch.setattr(f"{mod_name}.SentenceTransformer", MockTransformer)

    return MockTransformer
