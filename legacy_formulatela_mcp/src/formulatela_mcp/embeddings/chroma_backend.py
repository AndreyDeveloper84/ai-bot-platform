"""Chroma реализация EmbeddingStore.

Поддерживает 2 режима embedding:
- `provider="default"` (sentence-transformers all-MiniLM-L6-v2) — локально,
  без API. Используется в тестах и dev (~80MB модель кешируется).
- `provider="openai"` (text-embedding-3-small) — через прокси из
  `TELEGRAM_PROXY`/`OPENAI_PROXY` (api.openai.com заблокирован в РФ).
  Используется в проде.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

from .store import EmbeddingStore, IndexItem, SearchResult


logger = logging.getLogger(__name__)


def _build_embedding_function(provider: str):
    """Создаёт embedding_function для Chroma.

    provider:
    - "default" — DefaultEmbeddingFunction (all-MiniLM-L6-v2 sentence-transformers)
    - "openai" — OpenAIEmbeddingFunction(text-embedding-3-small) с прокси если задан
    """
    if provider == "default":
        return embedding_functions.DefaultEmbeddingFunction()

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY не задан — не могу использовать openai провайдер")

        # Прокси-aware: если задан proxy — конструируем httpx_client с ним и
        # передаём в OpenAI SDK через api_base override. Chroma's
        # OpenAIEmbeddingFunction принимает api_base + api_key, дальше внутри
        # использует openai SDK который сам не знает про прокси по env.
        # Workaround: ставим OPENAI_PROXY как https_proxy для текущего процесса
        # перед вызовом OpenAIEmbeddingFunction.
        proxy = os.environ.get("TELEGRAM_PROXY") or os.environ.get("OPENAI_PROXY")
        if proxy:
            # OpenAI SDK >=1.50 уважает HTTPS_PROXY env при создании дефолтного httpx-клиента
            os.environ.setdefault("HTTPS_PROXY", proxy)
            logger.info("OpenAI embeddings будут идти через прокси (HTTPS_PROXY=%s)", proxy[:20] + "...")

        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=api_key,
            model_name="text-embedding-3-small",
        )

    raise ValueError(f"Неизвестный embedding provider: {provider!r}")


class ChromaStore(EmbeddingStore):
    """Chroma persistent store для FAQ-эмбеддингов.

    Args:
        persist_path: каталог для filesystem persistence (создаётся если нет)
        collection_name: имя коллекции (для разных сущностей — разные коллекции)
        provider: "default" (локально, для тестов/dev) или "openai" (prod)
    """

    def __init__(
        self,
        persist_path: str,
        collection_name: str = "help_articles",
        provider: str = "default",
    ):
        self._persist_path = persist_path
        self._collection_name = collection_name
        self._provider = provider

        Path(persist_path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_path)
        self._embedding_fn = _build_embedding_function(provider)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, items: list[IndexItem]) -> None:
        if not items:
            return
        # Chroma 1.5+ требует non-empty metadata, иначе ValueError.
        # Подставляем placeholder для пустых dict'ов.
        metadatas = [
            i.metadata if i.metadata else {"_chroma_placeholder": "1"}
            for i in items
        ]
        # Chroma `add` падает на duplicate id, `upsert` — нет.
        self._collection.upsert(
            ids=[i.id for i in items],
            documents=[i.text for i in items],
            metadatas=metadatas,
        )

    def search(self, query: str, k: int = 3) -> list[SearchResult]:
        if k <= 0:
            return []
        actual_k = min(k, self.count()) if self.count() > 0 else 0
        if actual_k == 0:
            return []
        result = self._collection.query(
            query_texts=[query],
            n_results=actual_k,
            include=["documents", "metadatas", "distances"],
        )
        # Chroma возвращает {ids: [[...]], documents: [[...]], distances: [[...]], metadatas: [[...]]}
        ids = result["ids"][0]
        docs = result["documents"][0]
        metas = result["metadatas"][0] if result.get("metadatas") else [{}] * len(ids)
        dists = result["distances"][0]
        return [
            SearchResult(
                id=ids[i],
                text=docs[i],
                metadata=metas[i] or {},
                # cosine distance 0..2 → similarity 1..-1 (для одинаковых docs ≈1)
                score=max(0.0, 1.0 - dists[i]),
            )
            for i in range(len(ids))
        ]

    def delete_all(self) -> None:
        """Удалить collection полностью и пересоздать пустой (для full reindex)."""
        try:
            self._client.delete_collection(self._collection_name)
        except Exception:  # noqa: BLE001
            # collection может не существовать — это ОК
            pass
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self._collection.count()
