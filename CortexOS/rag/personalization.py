def personalized_score(
    retrieval_score: float,
    cosine_similarity: float,
    collaborative_signal: float,
    interactions_count: int,
    gamma_one: float = 0.15,
    gamma_two: float = 0.10,
) -> float:
    if interactions_count < 5:
        return retrieval_score
    return retrieval_score + (gamma_one * cosine_similarity) + (gamma_two * collaborative_signal)
