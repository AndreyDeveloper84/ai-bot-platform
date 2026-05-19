# Phase 5 Salon Knowledge — два параллельных трека: архитектурное сравнение

**Статус:** draft для обсуждения с командой.
**Автор:** ассистент, по запросу Андрея.
**Дата:** 2026-05-18.
**Цель:** свести в один документ две независимо растущие реализации Phase 5 Salon Knowledge / RAG, явно показать где они сходятся, где конфликтуют, и какие решения нужно принять до того, как разойдутся ещё дальше.

---

## TL;DR

Сейчас в команде существуют **две параллельные реализации Phase 5**, которые НЕ ссылаются друг на друга:

1. **Track A — `Shiro-Py/salon-knowledge`** (отдельный repo, делает молодой специалист). Это и есть «Service Knowledge Importer», задекларированный в описании Linear-проекта: standalone Django, full RAG-конвейер от сырых источников до retrieval API. Стек: **Qdrant + Anthropic + Salon-модель**. Покрытие: M1-M3 закрыты, M4 в работе.

2. **Track B — `ai-bot-platform/apps/kb`** (внутри основной платформы, шипал я по запросу «заполнять KB для всех салонов»). Это узкий shared-corpus слой: Google Docs → `global_kb` tenant → ChromaDB → retriever fallback. Стек: **ChromaDB + OpenAI + Tenant-модель**. Покрытие: 6 sub-issues + 2 infra PR (#120/#121/#122/#124/#125/#129/#135/#136/#137) — все merged.

Эти треки решают **разные задачи**, но используют **разные стеки и разные модели данных** для одной и той же предметной области (RAG для бьюти-салонов). До тех пор, пока они не пересеклись в проде, цены коллизии нет — но как только обоих надо запустить на одном продакшене, придётся выбирать или объединять.

---

## Что делает Track A (Shiro-Py/salon-knowledge)

Полный конвейер per-salon импорта из разнородных источников, с human-in-the-loop validation.

```
┌────────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐    ┌─────────┐    ┌──────────┐
│ Importers  │ →  │ Cleaner  │ →  │ LLM      │ →  │ Approve   │ →  │ Chunker │ →  │ Qdrant   │
│ (TXT/MD/   │    │          │    │ extract  │    │ queue     │    │ +       │    │ search   │
│  DOCX/PDF) │    │          │    │ (Claude) │    │ (RBAC)    │    │ embed   │    │ + rerank │
└────────────┘    └──────────┘    └──────────┘    └───────────┘    └─────────┘    └──────────┘
  YClients/                          JSON Schema    Markdown gen +    BM25 +        Retrieval
  2GIS/Sheets/                       validator      webhook out       vector RRF    API
  website
```

**Ключевые архитектурные решения:**
- **Salon-модель**, не Tenant — `Salon` + `SalonMembership(role=reviewer|admin)` + `SalonSettings(monthly_llm_budget_usd, source_precedence)` — своя multi-tenancy с RBAC внутри салона.
- **Schema-driven extraction** — `schemas/service.json`, `staff.json`, `policy.json`, `faq.json`. LLM получает schema, возвращает структурированные объекты, JSON Schema validator режет невалидные.
- **Cost guardrails** — `monthly_llm_budget_usd` per salon, `CostTracker`, `BudgetExceededError`. Каждый extract учитывается.
- **Approve queue с RBAC** — без явного approve контент не публикуется. Это human-in-the-loop gate перед выкладкой в retrieval.
- **Hybrid search**: BM25 через PostgreSQL `to_tsvector('russian', ...)` + vector через Qdrant, объединяются через RRF (Reciprocal Rank Fusion) с константой K=60.
- **Re-rank** через cross-encoder (`sentence-transformers`).
- **Versioned Qdrant collections**: `salon_{id}_v{n}` + alias `salon_{id}` для atomic swap при reindex.
- **PII redactor** через `natasha` (российский NER) — hard gate, документ с PII не попадает в Qdrant.

**Что осталось (M5/M6/M7):** eval harness, citation tracking, drift detection, observability, external connectors (YClients/2GIS/Sheets/website), production hardening, load test 100 salons, DR.

---

## Что делает Track B (ai-bot-platform/apps/kb)

Тонкий shared-corpus слой для всех салонов. Один источник истины для медицинских данных, протоколов, общего каталога услуг.

```
┌─────────────┐    ┌─────────────────────┐    ┌──────────┐    ┌──────────────────────────┐
│ Google Docs │ →  │ seed_kb_from_gdocs  │ →  │ ChromaDB │ →  │ KbRetriever              │
│ (4 шт)      │    │ → global_kb tenant  │    │          │    │ tenant ∪ global_kb merge │
│ public link │    │ (idempotent)        │    │          │    │ по cosine                │
└─────────────┘    └─────────────────────┘    └──────────┘    └──────────────────────────┘
```

**Ключевые архитектурные решения:**
- **Tenant-модель с `is_system=True`** — `global_kb` это служебный tenant, не настоящий салон. Защищён от удаления через `TenantAdmin`.
- **Один shared корпус**, не N копий — `~150` `KbDocument` rows на всех. Embedding cost растёт с контентом, не с числом салонов.
- **Source: только Google Docs** (через публичный `?format=md`, без service-account). Один формат, одна структура.
- **Retriever-fallback** для `doc_type ∈ {SERVICE, CONTRAINDICATION, HELP_ARTICLE}`: после tenant-query идёт второй query в global_kb, top-K merge по cosine. Security invariant: `MASTER/FAQ/LEGAL` остаются strictly per-tenant.
- **Idempotent re-seed** через SHA-256 checksum — повторный прогон cmd без изменений = 0 OpenAI calls.
- **Версионирование `KbDocument`** через `(tenant, source_uri, version)` unique — старые версии не мутируются, добавляется новая.
- **K6 Celery sweep** — асинхронный embedding pipeline, выбирает rows где `embedded_at IS NULL OR embedded_at < updated_at`.

**Что НЕ сделано** (и не было в скоупе): approval queue, multi-source connectors, hybrid search (только vector), re-rank, eval harness, drift detection, PII gate, cost tracker per salon, schema-driven extraction.

---

## Side-by-side

### Модель данных

| | Track A — Salon Knowledge | Track B — apps/kb |
|---|---|---|
| Tenant сущность | `Salon` (own model + RBAC через `SalonMembership`) | `Tenant` (общая платформенная) + `is_system` флаг |
| Структура контента | `KnowledgeDocument` + JSON Schema типы (service, staff, policy, faq) | `KbDocument` + `KbDocType` enum (6 значений) |
| Версионирование | Versioned Qdrant collections (`salon_{id}_v{n}`) | `(tenant, source_uri, version)` unique в Postgres |
| Approve workflow | Да, отдельная app, RBAC, webhooks | Нет — `seed_kb_from_gdocs` сразу пишет |

### Стек

| Слой | Track A | Track B |
|---|---|---|
| LLM | **Anthropic** (`claude-sonnet-4-6`) | **OpenAI** (`text-embedding-3-small`) |
| Vector store | **Qdrant** | **ChromaDB** |
| BM25 | PostgreSQL `to_tsvector('russian', ...)` | — |
| Re-rank | `sentence-transformers` (ms-marco-MiniLM) | — |
| PII | `natasha` (RU NER) | — |
| Connectors | YClients / 2GIS / Sheets / website (Playwright) | — |
| Importers | TXT / MD / DOCX / PDF | — |
| Source | Multi-source | Google Docs only (через public export) |

### Pipeline depth

| Этап | Track A | Track B |
|---|---|---|
| Multi-source ingestion | ✅ | ❌ (только Google Docs) |
| LLM-extraction в structured JSON | ✅ | ❌ |
| Schema validation | ✅ (jsonschema) | ❌ |
| Conflict resolution между источниками | ✅ | ❌ |
| Cost guardrails per salon | ✅ | ❌ |
| Human approval queue | ✅ | ❌ |
| Markdown rendering | ✅ | ❌ (raw text → chunks) |
| Chunker | ✅ (Markdown-aware, 512 token + 64 overlap) | ✅ (по разделам/подразделам Google Doc) |
| Embedding | ✅ | ✅ |
| Vector search | ✅ (Qdrant) | ✅ (ChromaDB) |
| BM25 | ✅ | ❌ |
| Re-rank | ✅ | ❌ |
| Citation tracking | планируется | ❌ |
| PII redaction | планируется | ❌ |
| Drift detection | планируется | ❌ |
| Eval harness | планируется | ❌ |
| Tenant fallback на shared корпус | ❌ | ✅ |

### Состояние

| | Track A | Track B |
|---|---|---|
| Repo | github.com/Shiro-Py/salon-knowledge | github.com/AndreyDeveloper84/ai-bot-platform (apps/kb) |
| README | нет | есть docs/operations/global-kb-tenant.md |
| Тесты | 14 файлов (`tests/test_*.py`) | 153+ unit + 5 smoke (opt-in) |
| CI | `.github/workflows/ci.yml` (свой) | репоный CI (pytest+ruff+mypy+replay) |
| Прод-готово | нет (M4 in progress, M5+ Backlog) | да на dev (187 KbDocument rows, 204 ChromaDB chunks) |

---

## Где сходятся, где не сходятся

**Сходятся семантически** (оба отвечают на «дай чанки по запросу»):
- chunking + embedding + vector search
- per-salon scoping (только реализовано разными моделями)
- цель — снизить hallucination в боте через grounded answers

**Не сходятся архитектурно:**
- `Salon` vs `Tenant` — две разные модели tenant-а
- Qdrant vs ChromaDB — два разных vector store
- Anthropic vs OpenAI — два разных LLM provider
- Approval queue vs прямой seed — две разные политики публикации
- Multi-source connectors vs Google Docs only — два разных пути в систему

**Не пересекаются по контенту**: Track A заточен под индивидуальные данные конкретного салона (мастера, прайс, расписание, FAQ). Track B заточен под shared cross-salon корпус (универсальный каталог процедур, противопоказания, aftercare protocol). Если оба запущены на одном проде, бот мог бы спрашивать ОБА: «маникюр у Анны в этом салоне» — Track A; «можно ли маникюр беременным» — Track B.

---

## Три сценария сосуществования

### Сценарий 1: «Сосуществуют параллельно с чётким раз делом»

- Track A остаётся per-salon repository: каждый салон импортирует свой контент через approve queue
- Track B остаётся shared-corpus в основной платформе для медицинских данных
- Бот ходит в оба источника: per-salon factuals (мастера, цены) → Track A retrieval API; общая медицина (противопоказания, aftercare) → Track B retriever

**Плюсы:** обе системы продолжают работать, никто не переделывает. Чёткое разделение responsibilities.
**Минусы:** два прод-стека (Qdrant + ChromaDB, Anthropic + OpenAI), два monitoring дашборда, два набора secrets, два budget'а. Сложнее эксплуатировать.
**Кому подходит:** если команда готова к ops-overhead и обе системы реально нужны.

### Сценарий 2: «Все в одном — поглощение»

Один трек становится канон, второй ассимилируется.

**2a. Track A поглощает B:** Phase 5 standalone становится единственной KB-системой. `apps/kb` в `ai-bot-platform` удаляется, Google Docs становится одним из importer-ов в Track A, `global_kb` становится особым `Salon=GLOBAL` или новой first-class сущностью «shared corpus». Бот ходит в Track A через retrieval API.

**Плюсы:** Один RAG-стек целиком. Approval, eval, PII, drift, citation tracking — всё это автоматически прикладывается к global_kb.
**Минусы:** Текущая работа (Sub-1..Sub-6) частично уходит в утиль — миграция тестов и данных на новую модель. Перевод `Tenant.is_system` логики на `Salon`.

**2b. Track B поглощает A:** `apps/kb` расширяется до полного конвейера Phase 5. JSON Schema importers, LLM extraction, approve queue, RBAC, hybrid search, re-rank — всё внутрь `ai-bot-platform`. Qdrant заменяет ChromaDB (или Qdrant добавляется как второй store), Anthropic добавляется как второй provider.

**Плюсы:** один deploy, одна tenant модель, переиспользуется существующая инфра (Celery, Sentry, observability).
**Минусы:** Track A это ~4 месяца работы для одного джуна. Поглотить = переписать большую часть. Junior может расстроиться.

### Сценарий 3: «Cancel one, keep the other»

Выбирается один трек как канон, второй останавливается полностью.

- Если **отменяется Track A**: 17 Done тикетов сжигаются. Junior разочарован. Но это самый быстрый путь к простоте.
- Если **отменяется Track B**: 7 merged PR откатываются. Текущий `global_kb` corpus уходит. Бот теряет fallback к shared медицине до тех пор, пока Track A не покроет M5+ и не наладит свой эквивалент.

**Кому подходит:** если команда уверена что одна архитектура явно правильная.

---

## Моя рекомендация (для затравки обсуждения)

**Сценарий 1 краткосрочно, миграция к 2a долгосрочно.**

Краткосрочно:
- Track A продолжает закрывать M4-M7 в своём repo. Это уже самостоятельная сложная система с подсистемами, которых в `apps/kb` нет (approval, PII, eval). Откатывать не стоит.
- Track B (`global_kb`) остаётся как есть — он уже работает и стоит ~50 строк bot-кода, чтобы fallback срабатывал. До тех пор, пока Track A не построил эквивалент shared-corpus поверх Salon-модели, Track B закрывает реальную потребность.
- В Linear прописываются связи: каждый M7-issue Phase 5 должен явно решать «как заменить ai-bot-platform/apps/kb global_kb», а не делать вид что его нет.

Долгосрочно (после стабильного M7):
- Track A поглощает B (вариант 2a). Конечная точка: один retrieval API, одна tenant-модель, одна vector БД. `apps/kb` удаляется когда Track A покрывает 100% его use case-ов (shared corpus + global fallback + Google Docs ingest).
- Stack-расхождения резолвятся: Qdrant выигрывает (Track A далеко зашёл), Anthropic vs OpenAI — отдельное обсуждение по cost/quality/RU-resilience.

---

## Что нужно решить с командой

1. **Это два решения одной задачи или две задачи?**
   - Если одна задача — какой трек канонен?
   - Если две — где провести границу?

2. **Стек: Qdrant vs ChromaDB.** Track A далеко зашёл на Qdrant. `ai-bot-platform` исторически на ChromaDB. Долгосрочно нужен один. Какой?

3. **LLM provider: Anthropic vs OpenAI.** То же самое — Track A на Claude, `ai-bot-platform` на OpenAI. Это вопрос cost/quality/RU-доступ (Anthropic в РФ доступен только через proxy так же как и OpenAI; quality для русского sometimes даёт Claude преимущество, но требует benchmark). Если решение «оба» — нужен router-абстракция.

4. **Multi-tenancy: `Salon` vs `Tenant`.** Track A построил RBAC роли (`reviewer`/`admin`) на уровне Salon. `ai-bot-platform` имеет flat-tenant + Django superuser. Кто прав? Это решение про **владение данными в салоне** — кто может одобрить knowledge, кто откатить версию, кто видеть QueryLog.

5. **Approval workflow: human-in-the-loop vs прямой seed.** Track A гейтит весь контент через approve queue с RBAC. Track B пишет напрямую (Google Docs владельцы и есть «авторитет»). Если merger — какая политика для shared corpus? для per-salon?

6. **Phase 5 в Linear — обновить трекинг:**
   - M0 в Backlog при том что M1-M3 Done — невозможно, закрыть
   - DRF-346..350 legacy stubs — заархивировать
   - Завести labels: `track-a-standalone`, `track-b-global-kb`, `convergence`
   - Сослаться на 7 KB-RAG PR в новом meta-issue «коэффициент пересечения с ai-bot-platform/apps/kb»

7. **Junior onboarding:** Shiro-Py/salon-knowledge без README. Минимальный quickstart + ссылка на эту таблицу облегчили бы code review и интеграцию с командой.

---

## Аппендикс: точечный inventory различий стека

Если идти по сценарию 2 (поглощение), вот что нужно резолвить:

| Подсистема | Track A | Track B | Что делать при merge |
|---|---|---|---|
| Vector store | qdrant-client 1.9 | chromadb (current ai-bot-platform) | Выбрать один; миграция данных при смене |
| LLM client | anthropic 0.28 | openai (текущая платформа) | Provider abstraction (router pattern) или один |
| Embedding model | text-embedding-3-small / voyage-3 | text-embedding-3-small | Совпадает только если оба на OpenAI |
| Tenant model | `Salon` + `SalonMembership` | `Tenant` + `is_system` | Один из двух; миграция данных |
| BM25 | Postgres `to_tsvector('russian')` | нет | Добавить в Track B при merge |
| Rerank | `sentence-transformers` (ms-marco) | нет | Добавить в Track B при merge |
| Importers | python-docx, pdfplumber, pytesseract, PIL | нет (Google Docs только) | Перенести из A |
| PII | `natasha` | нет | Перенести из A |
| Approve queue | own app + RBAC | нет (прямой seed) | Перенести из A для per-salon |
| Markdown gen | Jinja2 templates | нет (raw text) | Перенести из A |
| Versioning | Qdrant collection aliasing | (tenant, source_uri, version) unique | Унифицировать стратегию |

---

## Финал

Документ не диктует решение — обозначает поле для разговора. До тех пор, пока команда явно не выбрала сценарий 1/2/3, треки будут расходиться дальше: M5+ в Track A добавит eval/PII/drift, и эти возможности станут уникальной фичей `Salon-Knowledge`, недоступной через `apps/kb`. Чем дольше треки растут параллельно, тем дороже merger.
