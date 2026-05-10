"""T-03 RED: skeleton MCP-сервера — импортируется, ping() tool работает."""
import pytest


def test_main_module_importable():
    """from formulatela_mcp.main import mcp — без ImportError."""
    from formulatela_mcp.main import mcp, ping, cli
    assert mcp is not None
    assert callable(ping)
    assert callable(cli)


def test_ping_tool_returns_pong():
    """ping() напрямую возвращает 'pong'."""
    from formulatela_mcp.main import ping
    assert ping() == "pong"


def test_mcp_has_ping_tool_registered():
    """FastMCP зарегистрировал ping как tool (через @mcp.tool() декоратор)."""
    from formulatela_mcp.main import mcp
    # FastMCP API: list_tools / get_tools / _tools — структура зависит от версии SDK.
    # Проверяем самым устойчивым способом — атрибут tools или метод list.
    tool_names = []
    if hasattr(mcp, "_tool_manager"):
        tool_names = list(mcp._tool_manager._tools.keys())
    elif hasattr(mcp, "tools"):
        tool_names = list(mcp.tools.keys()) if hasattr(mcp.tools, "keys") else [t.name for t in mcp.tools]
    assert "ping" in tool_names, f"ping не найден в tools: {tool_names}"


def test_django_bootstrap_idempotent():
    from formulatela_mcp.django_bootstrap import setup_django
    setup_django()
    setup_django()  # second call no-op
    from django.conf import settings
    assert settings.configured


# ─── search_faq tool ────────────────────────────────────────────────────────


def test_search_faq_registered_as_tool():
    """FastMCP зарегистрировал search_faq."""
    from formulatela_mcp.main import mcp
    tool_names = list(mcp._tool_manager._tools.keys()) if hasattr(mcp, "_tool_manager") else []
    assert "search_faq" in tool_names


@pytest.mark.django_db(transaction=True)
def test_search_faq_returns_top_k_with_scores(tmp_path, monkeypatch):
    """search_faq возвращает k результатов в порядке убывания similarity."""
    from model_bakery import baker
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "default")

    # Наполняем HelpArticle и индексируем
    baker.make("services_app.HelpArticle", question="Как записаться?",
               answer="Через бота или по телефону.", is_active=True)
    baker.make("services_app.HelpArticle", question="Сколько стоит массаж?",
               answer="От 1500 рублей.", is_active=True)
    baker.make("services_app.HelpArticle", question="Когда вы работаете?",
               answer="9-21 без выходных.", is_active=True)

    from formulatela_mcp.embeddings.chroma_backend import ChromaStore
    from formulatela_mcp.embeddings.reindex import reindex_help_articles
    store = ChromaStore(persist_path=str(tmp_path), provider="default")
    reindex_help_articles(store)

    from formulatela_mcp.main import search_faq
    response = search_faq("Хочу записаться", k=2)
    # Возвращается dict-обёртка (FastMCP сериализует list[dict] странно)
    assert isinstance(response, dict)
    assert "results" in response
    results = response["results"]
    assert len(results) == 2
    # Топ — про запись (semantic match)
    assert "записаться" in results[0]["question"].lower()
    assert 0.0 <= results[0]["score"] <= 1.0
    assert results[0]["score"] >= results[1]["score"]  # убывание


@pytest.mark.django_db(transaction=True)
def test_search_faq_caps_k_to_10(tmp_path, monkeypatch):
    """k > 10 → принудительно 10 (защита от DOS)."""
    from model_bakery import baker
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "default")
    for i in range(15):
        baker.make("services_app.HelpArticle", question=f"Q{i}?",
                   answer=f"A{i}", is_active=True)
    from formulatela_mcp.embeddings.chroma_backend import ChromaStore
    from formulatela_mcp.embeddings.reindex import reindex_help_articles
    store = ChromaStore(persist_path=str(tmp_path), provider="default")
    reindex_help_articles(store)
    from formulatela_mcp.main import search_faq
    response = search_faq("anything", k=999)
    assert len(response["results"]) <= 10


@pytest.mark.django_db(transaction=True)
def test_search_faq_empty_store_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "default")
    from formulatela_mcp.main import search_faq
    assert search_faq("anything", k=3) == {"results": []}
