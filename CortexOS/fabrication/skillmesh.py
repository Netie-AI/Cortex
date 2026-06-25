from rank_bm25 import BM25Okapi
from .skill_registry import SkillRegistry, SkillCard

def _tokenize(text: str) -> list[str]:
    return text.lower().split()

class BM25Index:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self.cards = registry.all()
        
        corpus = []
        for card in self.cards:
            text_parts = [card.name, card.description] + card.example_intents
            text = " ".join(text_parts)
            corpus.append(_tokenize(text))
            
        if corpus:
            self.bm25 = BM25Okapi(corpus)
        else:
            self.bm25 = None
            
    def query(self, intent: str, top_n: int = 50) -> list[tuple[SkillCard, float]]:
        if not intent.strip() or not self.bm25 or not self.cards:
            return []
            
        tokenized_query = _tokenize(intent)
        scores = self.bm25.get_scores(tokenized_query)
        
        results = [
            (card, score)
            for card, score in zip(self.cards, scores)
            if score > 0
        ]
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_n]

import numpy as np
from sentence_transformers import SentenceTransformer
from netie.result import Ok, Err, Result

class DenseReranker:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)
        
    def rerank(self, intent: str, candidates: list[tuple[SkillCard, float]], top_k: int = 8) -> list[SkillCard]:
        cards, _ = self._rerank_with_scores(intent, candidates, top_k)
        return cards
        
    def _rerank_with_scores(self, intent: str, candidates: list[tuple[SkillCard, float]], top_k: int = 8) -> tuple[list[SkillCard], list[float]]:
        if not candidates:
            return [], []
            
        candidate_cards = [c for c, _ in candidates]
        candidate_texts = [f"{c.name}: {c.description}" for c in candidate_cards]
        
        # Encode
        intent_emb = self.model.encode(intent)
        candidate_embs = self.model.encode(candidate_texts)
        
        # Cosine similarity
        intent_norm = np.linalg.norm(intent_emb)
        if intent_norm == 0:
            return candidate_cards[:top_k], [0.0]*len(candidate_cards[:top_k])
            
        scores = []
        for emb in candidate_embs:
            emb_norm = np.linalg.norm(emb)
            if emb_norm == 0:
                scores.append(0.0)
            else:
                score = np.dot(intent_emb, emb) / (intent_norm * emb_norm)
                scores.append(float(score))
                
        # Sort by score
        scored_candidates = list(zip(candidate_cards, scores))
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        
        return [c for c, _ in scored_candidates[:top_k]], [s for _, s in scored_candidates[:top_k]]

class SkillMesh:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self.bm25_index = BM25Index(registry)
        self.reranker = DenseReranker()
        
    def retrieve(self, intent: str, top_k: int = 8) -> Result[list[SkillCard]]:
        # Stage 1
        bm25_results = self.bm25_index.query(intent, top_n=50)
        
        if not bm25_results:
            return Ok([])
            
        # Stage 2
        reranked_cards, reranked_scores = self.reranker._rerank_with_scores(intent, bm25_results, top_k)
        
        # Adversarial check / Near-boundary detection
        detail = {}
        if len(reranked_scores) > 1:
            stddev = np.std(reranked_scores)
            if stddev > 0.4:
                detail["warning"] = "near_boundary"
                top_k = min(top_k, 3)
                reranked_cards = reranked_cards[:top_k]
                
        res = Ok(reranked_cards)
        res.detail = detail
        return res
