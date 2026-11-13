"""Score fusion for hybrid retrieval.

Two strategies are supported and switchable at query time:

* ``weighted`` - min/max-normalise each path's scores, then
  ``alpha * bm25 + (1 - alpha) * vector``.
* ``rrf`` - Reciprocal Rank Fusion, ``sum(1 / (k + rank))`` over both paths.

Both return a dense fusion score array plus the ranks used, so the API can
expose a per-path score breakdown for every hit.
"""

import numpy as np


def normalize_scores(scores):
    """Scale a score vector into [0, 1] by its maximum positive value."""
    scores = np.asarray(scores, dtype=np.float64)
    if scores.size == 0:
        return scores
    top = float(scores.max())
    if top <= 0:
        return np.zeros_like(scores)
    return scores / top


def ranked_indices(scores):
    """Stable descending order; ties keep the original chunk order."""
    return np.argsort(-np.asarray(scores), kind="stable")


def weighted_fusion(bm25_scores, vector_scores, alpha):
    b = normalize_scores(bm25_scores)
    v = normalize_scores(vector_scores)
    return alpha * b + (1.0 - alpha) * v


def rrf_fusion(bm25_scores, vector_scores, k=60):
    size = len(np.asarray(bm25_scores))
    fused = np.zeros(size, dtype=np.float64)
    for scores in (bm25_scores, vector_scores):
        order = ranked_indices(scores)
        for rank, index in enumerate(order):
            fused[index] += 1.0 / (k + rank + 1)
    return fused


def path_ranks(scores):
    """Return the 1-based rank of every chunk inside one score path."""
    order = ranked_indices(scores)
    ranks = np.zeros(len(order), dtype=np.int64)
    for rank, index in enumerate(order):
        ranks[index] = rank + 1
    return ranks


def fuse(bm25_scores, vector_scores, strategy, alpha, rrf_k=60):
    if strategy == "rrf":
        fused = rrf_fusion(bm25_scores, vector_scores, k=rrf_k)
    else:
        fused = weighted_fusion(bm25_scores, vector_scores, alpha)
    return fused
