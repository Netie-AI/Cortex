from pathlib import Path
from netie.fabrication.skill_registry import load_skill_cards, SkillRegistry
from netie.fabrication.skillmesh import BM25Index

def test_bm25_index():
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    registry = SkillRegistry(load_skill_cards(skills_dir))
    index = BM25Index(registry)
    
    # Test queries
    res_summ = index.query("summarize this article", top_n=3)
    assert any(c.skill_id == "summarization_v1" for c, s in res_summ)
    
    res_search = index.query("search the web for news", top_n=3)
    assert any(c.skill_id == "web_search_v1" for c, s in res_search)
    
    # Empty query
    assert len(index.query("")) == 0
    
    # Top N limit
    all_res = index.query("test", top_n=2)
    assert len(all_res) <= 2

from netie.fabrication.skillmesh import DenseReranker, SkillMesh

def test_dense_reranker():
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    registry = SkillRegistry(load_skill_cards(skills_dir))
    index = BM25Index(registry)
    candidates = index.query("summarize", top_n=10)
    
    reranker = DenseReranker()
    res = reranker.rerank("summarize", candidates, top_k=3)
    assert isinstance(res, list)
    assert len(res) <= 3
    
def test_skillmesh():
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    registry = SkillRegistry(load_skill_cards(skills_dir))
    mesh = SkillMesh(registry)
    
    # 1. Normal retrieve
    res = mesh.retrieve("summarize this article", top_k=3)
    assert res.value is not None
    assert len(res.value) <= 3
    
def test_skillmesh_near_boundary(monkeypatch):
    """
    Near-boundary detection: artificially craft a nonsense intent and
    verify top_k is reduced to 3 (unit test with mock cosine scores)
    """
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    registry = SkillRegistry(load_skill_cards(skills_dir))
    mesh = SkillMesh(registry)
    
    # Force _rerank_with_scores to return artificially spread scores (stddev > 0.4)
    # to trigger the near_boundary check.
    def mock_rerank_scores(*args, **kwargs):
        cards = registry.all()
        # Create standard deviation > 0.4: scores [1.0]*3 + [0.0]*3 gives stddev 0.5
        scores = [1.0, 1.0, 1.0] + [0.0] * (len(cards) - 3)
        return cards, scores
    
    monkeypatch.setattr(mesh.reranker, "_rerank_with_scores", mock_rerank_scores)
    
    res = mesh.retrieve("search the web and calculate fibonacci and summarize and webhook", top_k=8)
    # Should get reduced to 3
    assert len(res.value) == 3
    assert res.detail.get("warning") == "near_boundary"
