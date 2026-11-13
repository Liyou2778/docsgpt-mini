"""In-memory hybrid search index built from SQLite.

The index holds chunk metadata, a BM25 inverted index and an L2-normalised
vector matrix.  Mutations call ``invalidate()``; the next query rebuilds the
index lazily (cheap for an MVP-sized corpus) so readers never see a partially
built index.
"""

import threading
import time

import numpy as np

from . import config, store
from .bm25 import BM25Index
from .hybrid import fuse, normalize_scores, path_ranks
from .tokenize import tokenize
from .vector import cosine_scores, embed_tokens


class SearchIndex:
    def __init__(self):
        self._state_lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._version = 0
        self._built_version = -1
        self.chunks = []
        self.vectors = np.zeros((0, config.VECTOR_DIM), dtype=np.float32)
        self.bm25 = BM25Index(config.BM25_K1, config.BM25_B)

    # ------------------------------------------------------------------ index
    def invalidate(self):
        with self._state_lock:
            self._version += 1

    @property
    def version(self):
        with self._state_lock:
            return self._version

    def _ensure(self):
        with self._state_lock:
            stale = self._version != self._built_version
        if stale:
            self._rebuild()

    def _rebuild(self):
        with self._build_lock:
            with self._state_lock:
                target = self._version
            rows = store.all_chunks_with_doc()
            chunks = []
            vectors = []
            for row in rows:
                vec = np.frombuffer(row["vector"], dtype=np.float32).astype(np.float32)
                if vec.size != config.VECTOR_DIM:
                    vec = embed_tokens(tokenize(row["text"]), dim=config.VECTOR_DIM)
                chunks.append(
                    {
                        "chunk_id": row["chunk_id"],
                        "doc_id": row["doc_id"],
                        "doc_title": row["doc_title"],
                        "filename": row["filename"],
                        "seq": row["seq"],
                        "text": row["text"],
                        "char_start": row["char_start"],
                        "char_end": row["char_end"],
                    }
                )
                vectors.append(vec)
            if vectors:
                matrix = np.vstack(vectors).astype(np.float32)
            else:
                matrix = np.zeros((0, config.VECTOR_DIM), dtype=np.float32)
            bm25 = BM25Index(config.BM25_K1, config.BM25_B).build(
                (chunk["chunk_id"], tokenize(chunk["text"])) for chunk in chunks
            )
            with self._state_lock:
                self.chunks = chunks
                self.vectors = matrix
                self.bm25 = bm25
                self._built_version = target

    def ensure_fresh(self):
        self._ensure()

    def stats(self):
        self._ensure()
        docs = {chunk["doc_id"] for chunk in self.chunks}
        return {
            "chunks": len(self.chunks),
            "documents": len(docs),
            "vector_dim": config.VECTOR_DIM,
            "bm25_terms": len(self.bm25.postings),
            "index_version": self._built_version,
        }

    # ----------------------------------------------------------------- search
    def search(self, question, top_k=None, strategy=None, alpha=None):
        started = time.perf_counter()
        top_k = int(top_k or config.TOP_K)
        top_k = max(1, min(top_k, 50))
        strategy = (strategy or config.DEFAULT_STRATEGY).lower()
        if strategy not in ("weighted", "rrf"):
            strategy = "weighted"
        alpha = config.DEFAULT_ALPHA if alpha is None else float(alpha)
        alpha = max(0.0, min(1.0, alpha))

        self._ensure()
        chunks = self.chunks
        query_tokens = tokenize(question)
        if chunks:
            bm25_scores = self.bm25.score(query_tokens)
            query_vec = embed_tokens(query_tokens)
            vector_scores = cosine_scores(self.vectors, query_vec)
        else:
            bm25_scores = np.zeros(0, dtype=np.float64)
            vector_scores = np.zeros(0, dtype=np.float64)

        fused = fuse(bm25_scores, vector_scores, strategy, alpha, config.RRF_K)
        bm25_norm = normalize_scores(bm25_scores)
        vector_norm = normalize_scores(vector_scores)
        bm25_rank = path_ranks(bm25_scores)
        vector_rank = path_ranks(vector_scores)

        order = np.argsort(-fused, kind="stable")[:top_k] if chunks else []
        hits = []
        for index in order:
            chunk = chunks[int(index)]
            hits.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "doc_id": chunk["doc_id"],
                    "doc_title": chunk["doc_title"],
                    "filename": chunk["filename"],
                    "seq": chunk["seq"],
                    "text": chunk["text"],
                    "char_start": chunk["char_start"],
                    "char_end": chunk["char_end"],
                    "score": round(float(fused[index]), 6),
                    "bm25_score": round(float(bm25_scores[index]), 6),
                    "vector_score": round(float(vector_scores[index]), 6),
                    "bm25_norm": round(float(bm25_norm[index]), 6),
                    "vector_norm": round(float(vector_norm[index]), 6),
                    "bm25_rank": int(bm25_rank[index]),
                    "vector_rank": int(vector_rank[index]),
                }
            )

        return {
            "question": question,
            "strategy": strategy,
            "alpha": round(alpha, 4),
            "top_k": top_k,
            "total_chunks": len(chunks),
            "query_terms": len(query_tokens),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "hits": hits,
        }


INDEX = SearchIndex()
