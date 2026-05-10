"""Reindex HelpArticle → embedding store.

Запуск:
    python -m formulatela_mcp.embeddings.reindex

На проде — после правки HelpArticle через Django admin (можно повесить
Django signal post_save на HelpArticle для автоматического reindex одного
документа в будущем — Фаза 2.2+).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from formulatela_mcp.django_bootstrap import setup_django

setup_django()

from services_app.models import HelpArticle  # noqa: E402

from .chroma_backend import ChromaStore  # noqa: E402
from .store import EmbeddingStore, IndexItem  # noqa: E402


logger = logging.getLogger(__name__)


def _help_article_to_index_item(article: HelpArticle) -> IndexItem:
    """Подготавливает один HelpArticle к индексированию.

    text включает question + answer чтобы ловить и формулировку клиента
    («как записаться?») и содержание ответа («запись через telegram-бота...»).
    """
    text = f"Вопрос: {article.question}\n\nОтвет: {article.answer}"
    return IndexItem(
        id=str(article.id),
        text=text,
        metadata={
            "question": article.question,
            "answer": article.answer,
        },
    )


def reindex_help_articles(store: EmbeddingStore) -> int:
    """Полный reindex active HelpArticle. Возвращает количество индексированных."""
    articles = list(HelpArticle.objects.active().order_by("id"))
    if not articles:
        logger.warning("reindex_help_articles: нет active HelpArticle, store очищен")
        store.delete_all()
        return 0

    items = [_help_article_to_index_item(a) for a in articles]
    store.delete_all()
    store.upsert(items)
    logger.info("reindex_help_articles: проиндексировано %d статей", len(items))
    return len(items)


def get_default_store_path() -> str:
    """Каталог для Chroma persistence. Override через CHROMA_PATH env."""
    default = "/var/lib/formulatela-mcp/chroma"
    return os.environ.get("CHROMA_PATH", default)


def main() -> None:
    """CLI entrypoint: `python -m formulatela_mcp.embeddings.reindex`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    provider = os.environ.get("EMBEDDING_PROVIDER", "default")
    store_path = get_default_store_path()
    Path(store_path).mkdir(parents=True, exist_ok=True)

    logger.info("Reindex: store=%s provider=%s", store_path, provider)
    store = ChromaStore(persist_path=store_path, provider=provider)
    count = reindex_help_articles(store)
    print(f"OK: {count} HelpArticle indexed at {store_path}")


if __name__ == "__main__":
    main()
