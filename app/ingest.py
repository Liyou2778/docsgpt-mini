"""Asynchronous ingestion pipeline.

``POST /api/docs`` inserts a document with status ``pending`` and returns
immediately; a single background worker thread transitions it through
``processing`` to ``ready`` (or ``failed``).  Duplicate uploads are
idempotent: the content hash is unique, so re-posting the same text returns
the existing document instead of creating a second copy.
"""

import hashlib
import queue
import threading

from . import chunking, config, store
from .search import INDEX
from .tokenize import normalize_text, tokenize
from .vector import embed_tokens

_TASK_QUEUE = queue.Queue()
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False


def document_hash(text):
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def create_document(title, text, filename=None, source_type="text"):
    """Create a pending document (idempotent on content hash)."""
    text = normalize_text(text)
    if not text.strip():
        raise ValueError("document content is empty")
    if len(text) > config.MAX_DOC_CHARS:
        raise ValueError("document is too large: %d characters" % len(text))
    content_hash = document_hash(text)
    existing = store.find_by_hash(content_hash)
    if existing:
        return existing, False
    doc_id = store.new_id()
    doc, created = store.create_document(
        doc_id, title, filename, source_type, text, content_hash
    )
    if created:
        enqueue(doc_id)
    return doc, created


def process_document(doc_id):
    """Run the chunk -> vectorize -> persist pipeline for one document."""
    doc = store.get_document(doc_id, include_content=True)
    if not doc:
        return
    store.set_status(doc_id, "processing")
    try:
        pieces = chunking.chunk_text(
            doc["content"], config.CHUNK_SIZE, config.CHUNK_OVERLAP_RATIO
        )
        if not pieces:
            raise ValueError("no extractable text after chunking")
        rows = []
        for seq, piece in enumerate(pieces):
            tokens = tokenize(piece["text"])
            vector = embed_tokens(tokens)
            rows.append(
                {
                    "seq": seq,
                    "text": piece["text"],
                    "start": piece["start"],
                    "end": piece["end"],
                    "token_count": len(tokens),
                    "vector": vector.tobytes(),
                }
            )
        store.replace_chunks(doc_id, rows)
        store.mark_ready(doc_id, len(rows), len(doc["content"]))
        INDEX.invalidate()
    except Exception as exc:  # keep the document visible with a failure reason
        store.set_status(doc_id, "failed", "%s: %s" % (type(exc).__name__, exc))


def enqueue(doc_id):
    _TASK_QUEUE.put(doc_id)


def _worker_loop():
    while True:
        doc_id = _TASK_QUEUE.get()
        try:
            process_document(doc_id)
        except Exception as exc:  # pragma: no cover - defensive
            try:
                store.set_status(doc_id, "failed", "worker: %s" % exc)
            except Exception:
                pass
        finally:
            _TASK_QUEUE.task_done()


def start_worker():
    """Start the ingestion thread (once) and recover unfinished documents."""
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if not _WORKER_STARTED:
            thread = threading.Thread(target=_worker_loop, name="ingest-worker", daemon=True)
            thread.start()
            _WORKER_STARTED = True
    store.reset_processing()
    for doc_id in store.unfinished_ids():
        enqueue(doc_id)


def worker_status():
    return {
        "started": _WORKER_STARTED,
        "queue_size": _TASK_QUEUE.qsize(),
    }
