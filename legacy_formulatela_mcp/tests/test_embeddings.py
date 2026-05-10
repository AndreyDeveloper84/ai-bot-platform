"""T-04: тесты embedding store + reindex.

Все тесты используют provider='default' (sentence-transformers all-MiniLM-L6-v2).
Без обращения к OpenAI API → CI-friendly. Модель кешируется локально на ~80MB
после первой загрузки.
"""
import shutil
import tempfile
from pathlib import Path

import pytest
from model_bakery import baker

from formulatela_mcp.embeddings.chroma_backend import ChromaStore
from formulatela_mcp.embeddings.store import IndexItem


@pytest.fixture
def tmp_store():
    """Свежий ChromaStore в tmpdir (полностью изолирован между тестами)."""
    tmpdir = tempfile.mkdtemp(prefix="chroma_test_")
    store = ChromaStore(persist_path=tmpdir, provider="default")
    yield store
    shutil.rmtree(tmpdir, ignore_errors=True)


# ─── ChromaStore базовые операции ───────────────────────────────────────────

def test_chroma_empty_count(tmp_store):
    assert tmp_store.count() == 0


def test_chroma_upsert_then_count(tmp_store):
    tmp_store.upsert([
        IndexItem(id="1", text="Запись на массаж спины", metadata={"q": "Q1"}),
        IndexItem(id="2", text="Цены на услуги", metadata={"q": "Q2"}),
    ])
    assert tmp_store.count() == 2


def test_chroma_search_returns_relevant_first(tmp_store):
    """Семантическая близость работает: запрос про запись → ближайший — про запись."""
    tmp_store.upsert([
        IndexItem(id="record", text="Как записаться на приём к мастеру", metadata={}),
        IndexItem(id="prices", text="Сколько стоит массаж", metadata={}),
        IndexItem(id="hours", text="Режим работы салона", metadata={}),
    ])
    results = tmp_store.search("Хочу записаться", k=3)
    assert len(results) == 3
    assert results[0].id == "record"


def test_chroma_search_score_in_unit_range(tmp_store):
    tmp_store.upsert([IndexItem(id="x", text="Привет мир", metadata={})])
    results = tmp_store.search("Привет", k=1)
    assert len(results) == 1
    # similarity (1 - cosine_distance) должна быть в [0, 1]
    assert 0.0 <= results[0].score <= 1.0


def test_chroma_upsert_idempotent(tmp_store):
    """Повторный upsert того же id не плодит дубли."""
    tmp_store.upsert([IndexItem(id="1", text="text v1", metadata={})])
    tmp_store.upsert([IndexItem(id="1", text="text v2", metadata={})])
    assert tmp_store.count() == 1
    results = tmp_store.search("text", k=1)
    assert results[0].text == "text v2"  # обновлено


def test_chroma_delete_all_clears(tmp_store):
    tmp_store.upsert([IndexItem(id="1", text="x", metadata={})])
    tmp_store.delete_all()
    assert tmp_store.count() == 0


def test_chroma_search_on_empty_returns_empty(tmp_store):
    assert tmp_store.search("anything", k=3) == []


def test_chroma_search_caps_k_to_collection_size(tmp_store):
    """k=10 на коллекции с 2 документами → возвращаем 2, не падаем."""
    tmp_store.upsert([
        IndexItem(id="1", text="a", metadata={}),
        IndexItem(id="2", text="b", metadata={}),
    ])
    results = tmp_store.search("a", k=10)
    assert len(results) == 2


# ─── reindex_help_articles ──────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
def test_reindex_help_articles_indexes_only_active(tmp_store):
    from formulatela_mcp.embeddings.reindex import reindex_help_articles
    a_active = baker.make("services_app.HelpArticle", question="Active Q", answer="A1", is_active=True)
    baker.make("services_app.HelpArticle", question="Inactive Q", answer="A2", is_active=False)
    n = reindex_help_articles(tmp_store)
    assert n == 1
    assert tmp_store.count() == 1
    results = tmp_store.search("Active", k=1)
    assert results[0].metadata["question"] == "Active Q"


@pytest.mark.django_db(transaction=True)
def test_reindex_clears_store_when_no_articles(tmp_store):
    from formulatela_mcp.embeddings.reindex import reindex_help_articles
    tmp_store.upsert([IndexItem(id="stale", text="old", metadata={})])
    n = reindex_help_articles(tmp_store)
    assert n == 0
    assert tmp_store.count() == 0


@pytest.mark.django_db(transaction=True)
def test_reindex_metadata_includes_question_and_answer(tmp_store):
    from formulatela_mcp.embeddings.reindex import reindex_help_articles
    baker.make(
        "services_app.HelpArticle",
        question="Как записаться?",
        answer="Через бот или по телефону.",
        is_active=True,
    )
    reindex_help_articles(tmp_store)
    results = tmp_store.search("Как записаться", k=1)
    assert results[0].metadata["question"] == "Как записаться?"
    assert results[0].metadata["answer"] == "Через бот или по телефону."
