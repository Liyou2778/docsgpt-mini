"""FastAPI application for docsgpt-mini.

Endpoints
---------
GET    /                      single-page UI (static files, no build step)
POST   /api/docs              ingest text (JSON) or a raw md/txt body
GET    /api/docs              list documents and their ingestion status
GET    /api/docs/{doc_id}     document detail (optionally with chunks)
DELETE /api/docs/{doc_id}     remove a document and its chunks
POST   /api/chat              hybrid retrieval + cited answer
POST   /api/search            retrieval only (score breakdown, no LLM)
GET    /api/health            health, llm provider and index stats
GET    /api/stats             corpus/index statistics
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, ingest, rag, store
from .llm import ANSWERER
from .search import INDEX

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app):
    store.init_db()
    ingest.start_worker()
    INDEX.invalidate()
    yield


app = FastAPI(title="docsgpt-mini", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=config.MAX_QUESTION_CHARS)
    top_k: int | None = Field(default=None, ge=1, le=50)
    strategy: str | None = Field(default=None, pattern="^(weighted|rrf)$")
    alpha: float | None = Field(default=None, ge=0.0, le=1.0)


def _default_title(text, filename):
    if filename:
        stem = Path(filename).stem.strip()
        if stem:
            return stem
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:60]
    return "untitled-document"


@app.get("/")
def index_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm": {"provider": ANSWERER.provider, "model": config.LLM_MODEL or "mock"},
        "documents": store.status_counts(),
        "ingest": ingest.worker_status(),
        "index": INDEX.stats(),
    }


@app.get("/api/stats")
def stats():
    return {
        "documents": store.status_counts(),
        "index": INDEX.stats(),
        "config": {
            "chunk_size": config.CHUNK_SIZE,
            "chunk_overlap_ratio": config.CHUNK_OVERLAP_RATIO,
            "vector_dim": config.VECTOR_DIM,
            "bm25_k1": config.BM25_K1,
            "bm25_b": config.BM25_B,
            "default_strategy": config.DEFAULT_STRATEGY,
            "default_alpha": config.DEFAULT_ALPHA,
            "rrf_k": config.RRF_K,
        },
    }


@app.post("/api/docs", status_code=202)
async def create_document(
    request: Request,
    title: str | None = Query(default=None),
    filename: str | None = Query(default=None),
):
    body = await request.body()
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    source_type = "file" if filename else "text"

    if content_type == "application/json":
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        text = payload.get("text") or payload.get("content") or ""
        title = payload.get("title") or title
        filename = payload.get("filename") or filename
        source_type = payload.get("source_type") or source_type
    else:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="body must be UTF-8 text")

    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    if not isinstance(title, str) or not title.strip():
        title = _default_title(text, filename)
    title = title.strip()
    if filename is not None:
        filename = Path(filename).name

    try:
        doc, created = ingest.create_document(
            title=title, text=text, filename=filename, source_type=source_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"document": doc, "created": created, "duplicate": not created}


@app.get("/api/docs")
def list_documents():
    documents = store.list_documents()
    return {"documents": documents, "count": len(documents), "statuses": store.status_counts()}


@app.get("/api/docs/{doc_id}")
def get_document(doc_id: str, include_chunks: bool = Query(default=False)):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    if include_chunks:
        doc["chunks"] = store.get_chunks(doc_id)
    return {"document": doc}


@app.delete("/api/docs/{doc_id}")
def delete_document(doc_id: str):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    deleted = store.delete_document(doc_id)
    INDEX.invalidate()
    return {"deleted": deleted, "doc_id": doc_id}


@app.post("/api/chat")
def chat(request: ChatRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    result = rag.answer_question(
        question,
        top_k=request.top_k,
        strategy=request.strategy,
        alpha=request.alpha,
    )
    return result


@app.post("/api/search")
def search(request: ChatRequest):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    return INDEX.search(
        question,
        top_k=request.top_k,
        strategy=request.strategy,
        alpha=request.alpha,
    )


@app.exception_handler(404)
async def not_found(_request, exc):
    return JSONResponse(status_code=404, content={"detail": getattr(exc, "detail", "not found")})
