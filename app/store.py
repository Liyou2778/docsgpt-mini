"""SQLite persistence layer.

Two tables: ``documents`` (one row per uploaded/pasted document, holding the
normalised source text and its ingestion status) and ``chunks`` (one row per
chunk with its character offsets and hashing vector).  Connections are opened
per operation; WAL mode keeps the background ingestion worker and API request
handlers from blocking each other.
"""

import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from . import config

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    filename      TEXT,
    source_type   TEXT NOT NULL DEFAULT 'text',
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    content       TEXT NOT NULL,
    content_hash  TEXT NOT NULL UNIQUE,
    char_count    INTEGER NOT NULL DEFAULT 0,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    ready_at      TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    text         TEXT NOT NULL,
    char_start   INTEGER NOT NULL,
    char_end     INTEGER NOT NULL,
    token_count  INTEGER NOT NULL DEFAULT 0,
    vector       BLOB NOT NULL,
    UNIQUE (doc_id, seq),
    FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS idx_docs_status ON documents (status);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:16]


def _connect():
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


def _row_to_doc(row, include_content=False):
    if row is None:
        return None
    doc = {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "source_type": row["source_type"],
        "status": row["status"],
        "error": row["error"],
        "content_hash": row["content_hash"],
        "char_count": row["char_count"],
        "chunk_count": row["chunk_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "ready_at": row["ready_at"],
    }
    if include_content:
        doc["content"] = row["content"]
    return doc


def find_by_hash(content_hash):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return _row_to_doc(row)
    finally:
        conn.close()


def create_document(doc_id, title, filename, source_type, content, content_hash):
    """Insert a document row.  Returns (doc, created)."""
    now = _now()
    with _LOCK:
        conn = _connect()
        try:
            try:
                conn.execute(
                    """INSERT INTO documents
                       (id, title, filename, source_type, status, content,
                        content_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                    (doc_id, title, filename, source_type, content, content_hash, now, now),
                )
                conn.commit()
                created = True
            except sqlite3.IntegrityError:
                conn.rollback()
                created = False
            row = conn.execute(
                "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            return _row_to_doc(row), created
        finally:
            conn.close()


def get_document(doc_id, include_content=False):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return _row_to_doc(row, include_content=include_content)
    finally:
        conn.close()


def list_documents():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [_row_to_doc(row) for row in rows]
    finally:
        conn.close()


def set_status(doc_id, status, error=None):
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """UPDATE documents
                   SET status = ?, error = ?, updated_at = ?,
                       ready_at = CASE WHEN ? = 'ready' THEN ? ELSE ready_at END
                   WHERE id = ?""",
                (status, error, _now(), status, _now(), doc_id),
            )
            conn.commit()
        finally:
            conn.close()


def replace_chunks(doc_id, rows):
    """Replace all chunks of a document atomically.

    Each row is a dict: seq, text, start, end, token_count, vector(bytes).
    """
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                """INSERT INTO chunks
                   (id, doc_id, seq, text, char_start, char_end, token_count, vector)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "%s-c%05d" % (doc_id, row["seq"]),
                        doc_id,
                        row["seq"],
                        row["text"],
                        row["start"],
                        row["end"],
                        row["token_count"],
                        row["vector"],
                    )
                    for row in rows
                ],
            )
            conn.commit()
        finally:
            conn.close()


def mark_ready(doc_id, chunk_count, char_count):
    with _LOCK:
        conn = _connect()
        try:
            now = _now()
            conn.execute(
                """UPDATE documents
                   SET status = 'ready', error = NULL, chunk_count = ?,
                       char_count = ?, updated_at = ?, ready_at = ?
                   WHERE id = ?""",
                (chunk_count, char_count, now, now, doc_id),
            )
            conn.commit()
        finally:
            conn.close()


def delete_document(doc_id):
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_chunks(doc_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY seq ASC", (doc_id,)
        ).fetchall()
        return [
            {
                "chunk_id": row["id"],
                "doc_id": row["doc_id"],
                "seq": row["seq"],
                "text": row["text"],
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "token_count": row["token_count"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def all_chunks_with_doc():
    """All chunks of ready documents, joined with their document metadata."""
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT c.id AS chunk_id, c.doc_id, c.seq, c.text,
                      c.char_start, c.char_end, c.vector, c.token_count,
                      d.title AS doc_title, d.filename
               FROM chunks c
               JOIN documents d ON d.id = c.doc_id
               WHERE d.status = 'ready'
               ORDER BY c.doc_id ASC, c.seq ASC"""
        ).fetchall()
        return [
            {
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "seq": row["seq"],
                "text": row["text"],
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "vector": row["vector"],
                "token_count": row["token_count"],
                "doc_title": row["doc_title"],
                "filename": row["filename"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def unfinished_ids():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status IN ('pending', 'processing') ORDER BY created_at ASC"
        ).fetchall()
        return [row["id"] for row in rows]
    finally:
        conn.close()


def reset_processing():
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE documents SET status = 'pending', error = NULL, updated_at = ? WHERE status = 'processing'",
                (_now(),),
            )
            conn.commit()
        finally:
            conn.close()


def status_counts():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM documents GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}
    finally:
        conn.close()
