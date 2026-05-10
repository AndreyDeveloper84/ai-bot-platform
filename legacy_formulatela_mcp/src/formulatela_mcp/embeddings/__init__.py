"""Embedding store для семантического поиска по HelpArticle (FAQ).

Абстракция (`store.py::EmbeddingStore`) + реализации:
- `chroma_backend.py::ChromaStore` — Chroma локально (default для MVP)
- (опц. позже) PgVector для prod scale

Reindex: `reindex.py::reindex_help_articles(store)`.
"""
from .store import EmbeddingStore, SearchResult
from .chroma_backend import ChromaStore

__all__ = ["EmbeddingStore", "SearchResult", "ChromaStore"]
