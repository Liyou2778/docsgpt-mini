"""A small, dependency-free BM25 implementation.

Uses the classic Okapi BM25 formula:

    score(q, d) = sum over terms t in q of
        IDF(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))

with IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5)).

The inverted index maps each token to a posting list of (chunk_index, tf).
Scores are returned as a numpy array aligned with ``chunk_ids``.
"""

import math
from collections import Counter

import numpy as np


class BM25Index:
    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.chunk_ids = []
        self.doc_len = []
        self.avgdl = 0.0
        self.doc_freq = Counter()
        self.postings = {}  # token -> [(chunk_index, tf), ...]
        self._idf = {}

    @property
    def size(self):
        return len(self.chunk_ids)

    def build(self, items):
        """``items`` is an iterable of ``(chunk_id, tokens)`` pairs."""
        self.chunk_ids = []
        self.doc_len = []
        self.doc_freq = Counter()
        self.postings = {}
        self._idf = {}
        for chunk_id, tokens in items:
            index = len(self.chunk_ids)
            self.chunk_ids.append(chunk_id)
            self.doc_len.append(len(tokens))
            for token, tf in Counter(tokens).items():
                self.doc_freq[token] += 1
                self.postings.setdefault(token, []).append((index, tf))
        total = sum(self.doc_len)
        self.avgdl = (total / len(self.doc_len)) if self.doc_len else 0.0
        return self

    def idf(self, token):
        cached = self._idf.get(token)
        if cached is not None:
            return cached
        n = len(self.chunk_ids)
        df = self.doc_freq.get(token, 0)
        value = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        self._idf[token] = value
        return value

    def score(self, query_tokens):
        scores = np.zeros(len(self.chunk_ids), dtype=np.float64)
        if not self.chunk_ids or not query_tokens:
            return scores
        counts = Counter(query_tokens)
        for token, qtf in counts.items():
            postings = self.postings.get(token)
            if not postings:
                continue
            idf = self.idf(token)
            for index, tf in postings:
                dl = self.doc_len[index]
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / (self.avgdl or 1.0))
                scores[index] += idf * (tf * (self.k1 + 1.0) / denom) * min(qtf, 3)
        return scores
