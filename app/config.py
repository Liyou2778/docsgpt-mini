"""Runtime configuration for docsgpt-mini.

Everything is env-overridable and paths are resolved relative to the project
root, so the app never contains hard-coded absolute paths.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("DOCSGPT_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = Path(os.environ.get("DOCSGPT_DB_PATH", str(DATA_DIR / "app.db")))
CORPUS_DIR = Path(os.environ.get("DOCSGPT_CORPUS_DIR", str(BASE_DIR / "corpus")))

# Chunking defaults (characters).
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "512"))
CHUNK_OVERLAP_RATIO = float(os.environ.get("CHUNK_OVERLAP_RATIO", "0.1"))
MAX_DOC_CHARS = int(os.environ.get("MAX_DOC_CHARS", "2000000"))

# Retrieval defaults.
VECTOR_DIM = int(os.environ.get("VECTOR_DIM", "512"))
BM25_K1 = float(os.environ.get("BM25_K1", "1.5"))
BM25_B = float(os.environ.get("BM25_B", "0.75"))
TOP_K = int(os.environ.get("TOP_K", "5"))
DEFAULT_STRATEGY = os.environ.get("DEFAULT_STRATEGY", "weighted")  # weighted | rrf
DEFAULT_ALPHA = float(os.environ.get("DEFAULT_ALPHA", "0.5"))
RRF_K = int(os.environ.get("RRF_K", "60"))

# LLM abstraction: mock by default so the app is fully offline.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock")  # mock | openai
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))

MAX_QUESTION_CHARS = int(os.environ.get("MAX_QUESTION_CHARS", "2000"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
