"""Deterministic hashing vectorizer (no embedding model, no vector database).

Each token is hashed into one of ``dim`` buckets with a signed contribution
(sublinear term frequency weight).  The resulting dense vector is L2
normalised.  Identical text always yields an identical vector, both within a
process and across processes/platforms, because only ``hashlib`` digests are
used (never Python's salted ``hash()``).
"""

import hashlib
import math
from collections import Counter

import numpy as np

from . import config
from .tokenize import tokenize


def _digest(token):
    data = token.encode("utf-8")
    return hashlib.blake2b(data, digest_size=8).digest()


def _bucket(token, dim):
    """Return (bucket_index, sign) for a token, deterministically."""
    raw = _digest(token)
    value = int.from_bytes(raw, "big")
    index = value % dim
    # Independent sign bit from a second digest so collisions partially cancel.
    sign = 1.0 if int.from_bytes(_digest("sign:" + token), "big") & 1 else -1.0
    return index, sign


def feature_weights(tokens):
    """Sublinear TF weights: 1 + ln(tf) for every distinct token."""
    counter = Counter(tokens)
    return {token: 1.0 + math.log(tf) for token, tf in counter.items()}


def embed_tokens(tokens, dim=None):
    dim = dim or config.VECTOR_DIM
    vec = np.zeros(dim, dtype=np.float32)
    for token, weight in feature_weights(tokens).items():
        index, sign = _bucket(token, dim)
        vec[index] += sign * weight
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def embed_text(text, dim=None):
    return embed_tokens(tokenize(text), dim=dim)


def cosine_scores(matrix, query_vec):
    """Both inputs are already L2-normalised, so a dot product is cosine."""
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    return matrix @ query_vec.astype(np.float32)
