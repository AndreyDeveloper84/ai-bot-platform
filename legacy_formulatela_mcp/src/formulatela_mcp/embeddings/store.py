"""Абстракция embedding store. Реализации не зависят от конкретного backend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    """Один результат semantic search.

    `id`        — стабильный ID документа (для FAQ — str(HelpArticle.id))
    `text`      — оригинальный документ (то что embedding'илось)
    `metadata`  — произвольный dict (например {"question": "...", "answer": "..."})
    `score`     — similarity 0..1, где 1 = идентичный (НЕ distance!)
    """
    id: str
    text: str
    metadata: dict
    score: float


@dataclass(frozen=True)
class IndexItem:
    """Один документ для upsert."""
    id: str
    text: str
    metadata: dict


class EmbeddingStore(Protocol):
    """Интерфейс vector store. Реализации — `ChromaStore` и т.п."""

    def upsert(self, items: list[IndexItem]) -> None:
        """Добавить или обновить документы. Идемпотентно по `id`."""
        ...

    def search(self, query: str, k: int = 3) -> list[SearchResult]:
        """Top-k документов отсортированных по убыванию similarity."""
        ...

    def delete_all(self) -> None:
        """Очистить store (для полного reindex)."""
        ...

    def count(self) -> int:
        """Число документов в store."""
        ...
