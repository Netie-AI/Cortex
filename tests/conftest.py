import sys

import pytest
from unittest.mock import MagicMock


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

    fake_st = MagicMock()
    fake_st.SentenceTransformer = MockTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    if "netie.fabrication.skillmesh" in sys.modules:
        monkeypatch.setattr(
            "netie.fabrication.skillmesh.SentenceTransformer",
            MockTransformer,
        )

    return MockTransformer
