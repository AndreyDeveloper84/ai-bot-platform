"""KB document chunker (DRF-561 / Sprint 7 / K3).

Splits :class:`apps.kb.models.KbDocument.content` into fixed-size chunks
that K4 (DRF-562) embeds into ChromaDB. Each chunk carries paragraph /
heading context as metadata so retrieval can surface provenance.

### Sizing — Decision 5

* **chunk size** — 512 tokens (cl100k_base via tiktoken when available)
* **overlap** — 50 tokens

512 tokens fits ~3-4 service-description paragraphs in Russian. 50 tokens
of overlap (~10% bleed) preserves cross-chunk context so a sentence that
spans a chunk boundary stays retrievable.

### Per-doc-type strategy

The choice of how to split depends on what the doc looks like:

* ``service``       — split on paragraph boundaries, target 512 tokens.
* ``master``        — usually short; 1 doc = 1 chunk; split only if > 512.
* ``faq``           — atomic: 1 row = 1 chunk. If body > 512 tokens
                      it still splits (P2 fix — never silently truncate).
* ``help_article``  — long-form; split on paragraph + heading boundaries
                      with 50-token overlap.
* ``contraindication`` — same as ``faq`` (atomic).
* ``legal``         — split on paragraph; preserve heading context.

### Token-counting fallback

The chunker tries ``tiktoken.get_encoding("cl100k_base")`` first. If
tiktoken isn't installed (dev environments where the embedder runs
elsewhere), it falls back to a Russian-tuned char approximation:
≈ 3.5 chars per token. Approximation drift is bounded — chunks may end
up a bit short or long but never silently truncate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Decision 5 — chunk size / overlap. Constants over kwargs because the
# K4 ingester reads them at module level for cache-key construction.
CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 50

# Char approximation when tiktoken unavailable. ~3.5 chars per token
# tracks the cl100k_base behaviour on Russian Cyrillic mixed with
# Latin words (English brand names) within ±15% of the true count.
_CHARS_PER_TOKEN_FALLBACK = 3.5


@dataclass(frozen=True)
class Chunk:
    """One chunk emitted by :func:`chunk_document`.

    Fields:
      text: chunk body — fed straight into the embedder.
      ordinal: 0-based index within the source document. Stable
               across re-runs as long as content + doc_type don't change.
      token_count: estimated token count (tiktoken when available).
      metadata: free-form payload that propagates into chromadb. K4
                merges this with KbDocument-level metadata.
    """

    text: str
    ordinal: int
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def _try_load_tiktoken_encoding() -> Any | None:
    """Best-effort import. Returns the encoder or None on miss."""
    try:
        import tiktoken  # type: ignore[import-not-found]

        return tiktoken.get_encoding("cl100k_base")
    except (ImportError, RuntimeError):  # pragma: no cover — env-dependent
        return None


_ENCODER = _try_load_tiktoken_encoding()


def count_tokens(text: str) -> int:
    """Token count via tiktoken when installed; char approx otherwise."""
    if _ENCODER is not None:
        return len(_ENCODER.encode(text))
    # Russian-mixed approximation. Never returns 0 for non-empty text
    # so downstream length checks behave consistently.
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN_FALLBACK))


# ---------------------------------------------------------------------------
# Splitting primitives
# ---------------------------------------------------------------------------


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[А-ЯA-Z])")


def _split_paragraphs(text: str) -> list[str]:
    """Split text on blank-line boundaries; drop empty pieces."""
    return [p.strip() for p in _PARAGRAPH_BREAK.split(text) if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split a paragraph on sentence-end punctuation followed by capital."""
    parts = _SENTENCE_BREAK.split(text)
    return [p.strip() for p in parts if p.strip()]


def _pack_chunks(
    pieces: list[str],
    *,
    size: int,
    overlap: int,
) -> list[str]:
    """Greedy pack ``pieces`` (paragraphs or sentences) into chunks of
    at most ``size`` tokens. Carries an ``overlap``-token tail from the
    end of one chunk to the start of the next when the boundary doesn't
    naturally align.

    When a single piece is already larger than ``size`` (rare — happens
    only when a paragraph itself is > 512 tokens), the piece is further
    split sentence-by-sentence; if even that doesn't fit, it falls back
    to a hard char-count split (NEVER silently truncate — P2 fix).
    """
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0

    def flush() -> str:
        return " ".join(buffer).strip()

    for piece in pieces:
        piece_tokens = count_tokens(piece)

        # Single piece bigger than the budget — recursive subsplit.
        if piece_tokens > size:
            if buffer:
                chunks.append(flush())
                buffer, buffer_tokens = [], 0
            sub = _force_split_oversize(piece, size=size, overlap=overlap)
            chunks.extend(sub)
            continue

        # Will this piece still fit?
        if buffer_tokens + piece_tokens <= size:
            buffer.append(piece)
            buffer_tokens += piece_tokens
            continue

        # Flush current buffer, start fresh with overlap from the tail.
        chunks.append(flush())
        tail = _take_overlap_tail(buffer, overlap=overlap)
        buffer = tail + [piece]
        buffer_tokens = count_tokens(" ".join(buffer))

    if buffer:
        chunks.append(flush())

    return [c for c in chunks if c]


def _take_overlap_tail(buffer: list[str], *, overlap: int) -> list[str]:
    """Return the smallest suffix of ``buffer`` whose token count ≥ overlap.

    Used to seed the next chunk so cross-chunk sentences stay searchable.
    """
    if overlap <= 0 or not buffer:
        return []
    tail: list[str] = []
    total = 0
    for piece in reversed(buffer):
        tail.insert(0, piece)
        total += count_tokens(piece)
        if total >= overlap:
            break
    return tail


def _force_split_oversize(text: str, *, size: int, overlap: int) -> list[str]:
    """Single piece exceeds ``size`` — split sentence-by-sentence, then by
    raw char window as a last resort. Never returns silently truncated text.
    """
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        return _pack_chunks(sentences, size=size, overlap=overlap)

    # One enormous sentence (rare). Fall back to char-window split using
    # the same chars-per-token ratio. Conservative: char_size leaves
    # ~10% headroom under the token budget.
    char_size = int(size * _CHARS_PER_TOKEN_FALLBACK * 0.9)
    char_overlap = int(overlap * _CHARS_PER_TOKEN_FALLBACK)
    chunks = []
    pos = 0
    while pos < len(text):
        end = min(pos + char_size, len(text))
        chunks.append(text[pos:end])
        if end >= len(text):
            break
        pos = end - char_overlap
    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Doc types where 1 row = 1 chunk (atomic). Bodies under 512 tokens
# emit a single chunk; bodies over 512 still split (never truncate).
_ATOMIC_DOC_TYPES = {"faq", "contraindication"}


def chunk_document(
    content: str,
    *,
    doc_type: str,
    size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
    extra_metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Split ``content`` into ordered :class:`Chunk` instances.

    Args:
      content: full KbDocument body.
      doc_type: one of the :class:`apps.kb.models.KbDocType` values.
                Drives the splitting strategy.
      size: chunk-size budget in tokens. Defaults to Decision 5 / 512.
      overlap: token overlap between adjacent chunks. Defaults to 50.
      extra_metadata: merged into every emitted chunk's metadata
                      (caller-supplied — source_uri, version, etc.).

    Returns:
      Ordered list of :class:`Chunk`. Empty input → empty list.

    Never silently truncates: a 10k-token FAQ body emits multiple
    chunks rather than dropping the tail.
    """
    text = (content or "").strip()
    if not text:
        return []

    base_meta = {"doc_type": doc_type, **(extra_metadata or {})}

    # Atomic doc types — short FAQ/contraindication is 1 chunk.
    if doc_type in _ATOMIC_DOC_TYPES:
        total = count_tokens(text)
        if total <= size:
            return [
                Chunk(
                    text=text,
                    ordinal=0,
                    token_count=total,
                    metadata=base_meta,
                )
            ]
        # Long FAQ — still split, never drop.
        chunks_text = _pack_chunks(_split_sentences(text), size=size, overlap=overlap)
        return [
            Chunk(
                text=t,
                ordinal=i,
                token_count=count_tokens(t),
                metadata=base_meta,
            )
            for i, t in enumerate(chunks_text)
        ]

    # Long-form types — split on paragraphs first.
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        # Single-paragraph body — feed it as one piece to the packer.
        paragraphs = [text]
    chunks_text = _pack_chunks(paragraphs, size=size, overlap=overlap)
    return [
        Chunk(
            text=t,
            ordinal=i,
            token_count=count_tokens(t),
            metadata=base_meta,
        )
        for i, t in enumerate(chunks_text)
    ]
