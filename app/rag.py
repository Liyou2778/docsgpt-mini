"""Retrieval-augmented answering with traceable citations.

The answer text uses ``[n]`` markers where n is the rank of the retrieved
passage.  ``citations`` maps each marker to the real ``chunk_id`` plus the
chunk text and character offsets, so the UI can jump to and highlight the
original passage.
"""

import re
import time

from .llm import ANSWERER, CITATION_RE


def _citation_entry(marker, hit):
    return {
        "index": marker,
        "chunk_id": hit["chunk_id"],
        "doc_id": hit["doc_id"],
        "doc_title": hit["doc_title"],
        "seq": hit["seq"],
        "text": hit["text"],
        "char_start": hit["char_start"],
        "char_end": hit["char_end"],
        "score": hit["score"],
        "bm25_score": hit["bm25_score"],
        "vector_score": hit["vector_score"],
        "bm25_rank": hit["bm25_rank"],
        "vector_rank": hit["vector_rank"],
    }


def answer_question(question, top_k=None, strategy=None, alpha=None, index=None):
    """Run retrieval, generate an answer, and return the full API payload."""
    from .search import INDEX

    index = index or INDEX
    started = time.perf_counter()
    retrieval = index.search(question, top_k=top_k, strategy=strategy, alpha=alpha)
    hits = retrieval["hits"]

    contexts = [
        {
            "index": position + 1,
            "chunk_id": hit["chunk_id"],
            "title": hit["doc_title"],
            "text": hit["text"],
        }
        for position, hit in enumerate(hits)
    ]
    llm_output = ANSWERER.generate(question, contexts)
    answer = llm_output["text"]

    markers = [int(value) for value in CITATION_RE.findall(answer)]
    citations = []
    seen = set()
    for marker in markers:
        if marker in seen:
            continue
        if marker < 1 or marker > len(hits):
            continue
        seen.add(marker)
        citations.append(_citation_entry(marker, hits[marker - 1]))
    citations.sort(key=lambda item: item["index"])

    resolvable = sum(1 for marker in markers if 1 <= marker <= len(hits))
    traceability = round(resolvable / len(markers), 4) if markers else 1.0

    return {
        "question": question,
        "answer": answer,
        "citations": citations,
        "retrieval": retrieval,
        "llm": llm_output,
        "traceability": {
            "markers": len(markers),
            "resolved": resolvable,
            "rate": traceability,
            "citation_count": len(citations),
        },
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
