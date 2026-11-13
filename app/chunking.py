"""Character-based chunking with configurable size and overlap.

The default window is 512 characters with a 10% overlap.  Cuts are snapped to
sentence boundaries whenever one exists inside the trailing part of the
window, so retrieved chunks read as whole sentences instead of starting
mid-word.  Every chunk keeps its exact ``start``/``end`` offsets in the
normalised source text, which lets the UI show the original passage and lets
citations point back to a character range.
"""

import re

from . import config
from .tokenize import normalize_text

_BOUNDARY_RE = re.compile(r"[。！？!?；;\n]")


def _sentence_ends(text):
    """Offsets just after each sentence-ending character."""
    return [match.end() for match in _BOUNDARY_RE.finditer(text)]


def _snap_start(text, target, minimum, ends):
    """Move a chunk start forward to the nearest sentence boundary >= target."""
    for end in ends:
        if end >= target and end > minimum:
            return end
    return target


def _snap_end(text, target, minimum, ends):
    """Pull a chunk end back to the nearest sentence boundary <= target."""
    candidate = None
    for end in ends:
        if minimum < end <= target:
            candidate = end
    return candidate if candidate is not None else target


def chunk_text(raw_text, chunk_size=None, overlap_ratio=None, min_chunk_size=24):
    """Split text into chunks.  Returns a list of dicts with offsets."""
    text = normalize_text(raw_text)
    chunk_size = int(chunk_size or config.CHUNK_SIZE)
    overlap_ratio = config.CHUNK_OVERLAP_RATIO if overlap_ratio is None else float(overlap_ratio)
    overlap = max(0, int(chunk_size * overlap_ratio))

    if not text.strip():
        return []
    if len(text) <= chunk_size:
        stripped_start = len(text) - len(text.lstrip())
        stripped_end = len(text.rstrip())
        if stripped_end <= stripped_start:
            return []
        return [
            {
                "text": text[stripped_start:stripped_end],
                "start": stripped_start,
                "end": stripped_end,
            }
        ]

    ends = _sentence_ends(text)
    chunks = []
    start = 0
    guard = 0
    while start < len(text) and guard < 100000:
        guard += 1
        hard_end = min(start + chunk_size, len(text))
        if hard_end >= len(text):
            end = len(text)
        else:
            # Only snap backwards if the boundary is not too early (>= half window).
            end = _snap_end(text, hard_end, start + int(chunk_size * 0.5), ends)
        piece = text[start:end]
        piece_start = start + (len(piece) - len(piece.lstrip()))
        piece_end = start + len(piece.rstrip())
        if piece_end - piece_start >= min_chunk_size or (start == 0 and piece_end > piece_start):
            chunks.append(
                {
                    "text": text[piece_start:piece_end],
                    "start": piece_start,
                    "end": piece_end,
                }
            )
        if end >= len(text):
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        snapped = _snap_start(text, next_start, start, ends)
        if snapped >= end:
            snapped = next_start
        start = max(snapped, start + 1)

    return chunks
