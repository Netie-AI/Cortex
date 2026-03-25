import pytest
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def mock_sentence_transformer(monkeypatch):
    """
    Mocks sentence_transformers.SentenceTransformer globally so that test runs
    do not attempt to download or load the 80MB all-MiniLM-L6-v2 embedding model.
    """
    class MockTransformer:
        def __init__(self, *args, **kwargs):
            pass
        
        def encode(self, sentences, *args, **kwargs):
            # return dummy vectors (list of floats)
            if isinstance(sentences, str):
                return [0.1] * 384
            return [[0.1] * 384 for _ in sentences]

    # Patch at the module level where it is used
    try:
        import netie.fabrication.skillmesh
        monkeypatch.setattr("netie.fabrication.skillmesh.SentenceTransformer", MockTransformer)
    except ImportError:
        pass
    
    return MockTransformer
