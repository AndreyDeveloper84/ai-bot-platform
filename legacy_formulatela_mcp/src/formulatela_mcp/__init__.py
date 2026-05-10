"""MCP-сервер для AI-помощника MAX-бота formulatela58.ru.

Standalone-процесс, спавнится maxbot'ом через stdio (см.
docs/plans/maxbot-phase2-research-T01.md §1).

Tools (по мере реализации Фазы 2.1+):
- ping() — sanity check (T-03, текущий)
- search_faq(query, k) — top-k HelpArticle через embeddings (T-05)
- search_services(symptoms) — поиск услуг по описанию (Фаза 2.2)
- find_master / find_slot / book_via_yclients (Фаза 2.3)
"""
__version__ = "0.1.0"
