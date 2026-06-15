from netie.rag.fuser_rrf import fuse_dense_sparse, rrf_fuse
from netie.rag.retriever_dense import DenseHit
from netie.rag.personalization import personalized_score
from netie.rag.retriever_sparse import SparseHit


def test_rrf_fuse_combines_dense_and_sparse():
    dense = ["l1", "l2", "l3"]
    sparse = ["l3", "l1", "l4"]
    out = rrf_fuse([dense, sparse], k=60, top_n=3)
    ids = [doc_id for doc_id, _ in out]
    assert "l1" in ids
    assert "l3" in ids


def test_personalized_score_applies_after_minimum_interactions():
    assert personalized_score(1.0, 0.5, 0.5, interactions_count=1) == 1.0
    assert personalized_score(1.0, 0.5, 0.5, interactions_count=5) > 1.0


def test_fuse_dense_sparse_merges_by_listing_id():
    dense = [
        DenseHit(doc_id="l1", score=0.9, payload={"title": "A"}),
        DenseHit(doc_id="l2", score=0.7, payload={"title": "B"}),
    ]
    sparse = [
        SparseHit(listing_id="l2", score=0.95, payload={"title": "B2"}),
        SparseHit(listing_id="l3", score=0.5, payload={"title": "C"}),
    ]
    fused = fuse_dense_sparse(dense, sparse, k=60, top_n=5)
    ids = [h.listing_id for h in fused]
    assert "l2" in ids
    assert "l1" in ids
    assert "l3" in ids
