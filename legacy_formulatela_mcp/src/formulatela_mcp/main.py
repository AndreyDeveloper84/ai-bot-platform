"""MCP-сервер entrypoint.

Запуск:
    python -m formulatela_mcp.main          # stdio (для subprocess из maxbot)
    formulatela-mcp                          # альтернативный CLI (см. pyproject [project.scripts])
"""
from __future__ import annotations

# Django до импорта tools/ — те читают ORM
from formulatela_mcp.django_bootstrap import setup_django
setup_django()

from mcp.server.fastmcp import FastMCP  # noqa: E402


mcp = FastMCP("formulatela-mcp")


@mcp.tool()
def ping() -> str:
    """Sanity check — MCP-сервер жив и доступен из клиента.

    Используется maxbot'ом при инициализации persistent-сессии для проверки
    что subprocess успешно поднялся.
    """
    return "pong"


# Singleton ChromaStore — instance один на subprocess, переиспользуется на
# каждый call_tool. Без этого chromadb client (~600MB heap) пересоздавался
# на КАЖДЫЙ запрос — главная причина 2.4s gap в latency-логах.
_chroma_store_singleton = None


def _get_chroma_store():
    global _chroma_store_singleton
    if _chroma_store_singleton is not None:
        return _chroma_store_singleton
    from formulatela_mcp.embeddings.chroma_backend import ChromaStore
    from formulatela_mcp.embeddings.reindex import get_default_store_path
    import os
    provider = os.environ.get("EMBEDDING_PROVIDER", "default")
    _chroma_store_singleton = ChromaStore(
        persist_path=get_default_store_path(),
        provider=provider,
    )
    return _chroma_store_singleton


@mcp.tool()
def search_faq(query: str, k: int = 3) -> dict:
    """Найти top-k FAQ-статей семантически близких к query.

    Использует embeddings (см. embeddings/chroma_backend.py). Возвращает
    список dict'ов отсортированных по убыванию similarity. Каждый dict
    содержит:
        - question: str — формулировка вопроса
        - answer: str — ответ для клиента
        - score: float — similarity 0..1, где 1 = идеальное совпадение

    LLM-агент (вызывающий этот tool) должен сам решить какой score
    считать «достаточным» — рекомендуем порог 0.6+.

    Args:
        query: текст вопроса клиента (например "как записаться?")
        k: сколько результатов вернуть (1..10)
    """
    k = max(1, min(k, 10))
    store = _get_chroma_store()  # singleton, без re-init
    results = store.search(query, k=k)
    # Возвращаем dict-обёртку (НЕ list) — FastMCP сериализует list[dict] странно
    # (только первый элемент в content[0]). Dict гарантированно один JSON.
    return {
        "results": [
            {
                "question": r.metadata.get("question", ""),
                "answer": r.metadata.get("answer", ""),
                "score": round(r.score, 3),
            }
            for r in results
        ]
    }


# T-XX (Фаза 2.2): search_services
# T-XX (Фаза 2.3): find_master, find_slot, book_via_yclients


def cli() -> None:
    """Entry-point для console_script (см. pyproject)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    cli()
