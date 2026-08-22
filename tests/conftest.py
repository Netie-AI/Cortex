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


@pytest.fixture(autouse=True)
def auto_bind_warehouse_manifest(request):
    """Bind a warehouse-covering VerifiedManifest so existing answer tests keep
    answering after /dms/query requires verified. Opt out with
    ``@pytest.mark.no_auto_manifest``. Never used as a production grant.
    """
    path = str(getattr(request, "path", "") or "")
    in_answer_suite = "/tests/dms/" in path.replace("\\", "/") or "/tests/test_dms/" in path.replace(
        "\\", "/"
    )
    if not in_answer_suite or request.node.get_closest_marker("no_auto_manifest"):
        yield
        return

    from CortexOS.execution.session_manifests import (
        SessionUnbound,
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from tests.dms.session_manifest import bind_warehouse_session

    registry = get_session_registry()
    original = registry.resolve

    def resolve(session_id, *, now=None):
        try:
            return original(session_id, now=now)
        except SessionUnbound:
            sid = (session_id or "demo").strip() or "demo"
            bind_warehouse_session(sid)
            return original(sid, now=now)

    registry.resolve = resolve  # type: ignore[method-assign]
    bind_warehouse_session("demo")
    yield
    registry.resolve = original  # type: ignore[method-assign]
    reset_session_registry_for_tests()
