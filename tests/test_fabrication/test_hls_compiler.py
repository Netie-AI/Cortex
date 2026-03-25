import pytest
import respx
import httpx
from netie.fabrication.hls_compiler import HLSCompiler
from netie.fabrication.skillmesh import SkillMesh, DenseReranker, BM25Index
from netie.fabrication.skill_registry import SkillRegistry
from netie.result import Ok, Err

@pytest.fixture
def mock_mesh():
    # just dummy
    class MockMesh:
        def retrieve(self, intent, top_k=8):
            return Ok([])
    return MockMesh()

@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_hls_compiler_valid(mock_mesh, monkeypatch):
    compiler = HLSCompiler(mock_mesh)
    compiler.config.api_key = "test"
    compiler.config.synthesis_model = "gpt-4"
    
    valid_dsl = """
    {
      "version": "1.0",
      "entry_node_id": "start",
      "output_node_id": "end",
      "nodes": [
        {"id": "start", "type": "TOOL_CALL", "tier": 0, "inputs": []},
        {"id": "end", "type": "EMIT", "tier": 0, "inputs": ["start"]}
      ]
    }
    """
    
    async def mock_send(self, request, *args, **kwargs):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid_dsl}}]},
            request=request
        )

    monkeypatch.setattr("httpx.AsyncClient.send", mock_send)
    
    res = await compiler.synthesize("test intent")
    assert isinstance(res, Ok)
    assert res.value.intent_hash != "" 

@pytest.mark.asyncio
async def test_hls_compiler_retry(mock_mesh, monkeypatch):
    compiler = HLSCompiler(mock_mesh)
    compiler.config.api_key = "test"
    compiler.config.synthesis_model = "gpt-4"
    
    invalid_dsl = "NOT JSON"
    call_events = []
    
    async def mock_send(self, request, *args, **kwargs):
        call_events.append(1)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": invalid_dsl}}]},
            request=request
        )

    monkeypatch.setattr("httpx.AsyncClient.send", mock_send)
    
    res = await compiler.synthesize("test intent")
    assert isinstance(res, Err)
    # Ensure it tried exactly 2 times (initial + 1 retry)
    assert len(call_events) == 2
    
@pytest.mark.asyncio
@respx.mock
async def test_hls_compiler_token_truncation():
    # create a mesh that returns a lot of mock cards
    from netie.fabrication.skill_registry import SkillCard
    cards = [
        SkillCard(skill_id=f"card_{i}", name="Test", description="a " * 1000, category="cat", example_intents=[], required_tools=[])
        for i in range(5)
    ]
    
    class MockMeshObj:
        def retrieve(self, intent, top_k=8):
             return Ok(cards)
             
    mesh = MockMeshObj()
    compiler = HLSCompiler(mesh)
    
    text = compiler._render_and_truncate_skills(cards, max_tokens=2000)
    toks = compiler._measure_tokens(text)
    
    assert toks <= 2000
