# formulatela-mcp

MCP-сервер для AI-помощника MAX-бота **formulatela58.ru**. Standalone-процесс, спавнится `maxbot`'ом через stdio (подробности в `docs/plans/maxbot-phase2-research-T01.md`).

## Установка (локально)

```bash
# Из корня репо
cd services/formulatela_mcp
pip install -e .
```

## Запуск

```bash
# stdio (для subprocess-клиентов типа maxbot/handlers/ai_assistant.py)
python -m formulatela_mcp.main

# Или через CLI-script
formulatela-mcp
```

**Важно:** запускать НУЖНО с правильным venv (`.venv312` основного проекта),
чтобы был доступ к Django ORM (модели `services_app`).

## Тестирование вручную

С использованием стандартного MCP CLI:
```bash
mcp dev python -m formulatela_mcp.main  # MCP Inspector в браузере
```

Или через Python-клиент (см. `tests/test_main.py`).

## Tools (доступны в текущей версии)

| Tool | Args | Возвращает |
|---|---|---|
| `ping` | — | `"pong"` |

Дальше (по плану `docs/plans/maxbot-phase2-ai-mcp.md`):
- `search_faq(query, k=3)` — top-k HelpArticle через embeddings (T-05)
- `search_services(symptoms)` — поиск услуг (Фаза 2.2)
- `find_master`, `find_slot`, `book_via_yclients` (Фаза 2.3)

## Зависимости

- `mcp[cli]>=2026.1.0` — основной SDK
- `chromadb>=0.5` — vector store для FAQ embeddings (T-04)
- `openai>=1.50` — embedding generation (через прокси из `OPENAI_PROXY`)
- Django ORM подтягивается из родительского проекта (см. `django_bootstrap.py`)
