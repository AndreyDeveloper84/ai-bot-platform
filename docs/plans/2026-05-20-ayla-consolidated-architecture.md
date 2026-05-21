# Ayla — Consolidated Architecture & MVP Plan

> **Дата:** 2026-05-20
> **Автор:** Claude Opus 4.7 (1M context)
> **⚠️ Статус: SUPERSEDED for decision-making (2026-05-20).** This document remains as **audit/rationale background**. The 5 open questions in §5 have been answered. The architecture itself is now codified in **ADR-0009** (`docs/adr/ADR-0009-ayla-split-domain-architecture.md`). Execution lives in **Phase 0 Sprint Plan** (`docs/plans/2026-05-20-phase-0-sprint-plan.md`).
> Do NOT use this document as active source of truth. Use ADR-0009 for architecture decisions, Phase 0 Sprint Plan for execution. This doc is preserved so future engineers can read the reasoning behind ADR-0009 (variants B and C, why they were rejected, what trade-offs were considered).
> **Источники:** Notion (7 документов Ayla namespace), Linear (проект Ayla + 8 связанных проектов команды DRF), полный аудит трёх репозиториев: `ai-bot-platform`, `Ayla` (Django + Expo), `ayla-ai-core`

---

## TL;DR

1. **Что есть Ayla**: персональный AI-ассистент качества жизни. Memory-first. Два мобильных приложения (Ayla + Ayla Pro). Beauty = точка входа. Пилот Пенза, далее Казахстан. Монетизация = 10% комиссия + YooKassa в MVP.
2. **Что есть в коде**: три репо, не один. Они построены параллельно и сильно пересекаются по доменам (booking, payments, tenancy, conversations). `ayla-ai-core` — pure library v0.8.1, готовая к v1.0. `Ayla djangoproject` — booking-движок DDD + YooKassa + Expo-фронт. `ai-bot-platform` — оказался не каналом, а полноценным сервером (26 апп, multi-tenant, ChromaDB RAG, two-bus events, MAX Mini App).
3. **Главный архитектурный вопрос**: какой из двух Django-репо канонизирует, какой обслуживает. Три варианта в §5. Решение нужно до любого MVP-кода.
4. **MVP по Notion (P0)**: 12 user stories — booking + AI-чат с intent + master management. Уже сделано в Linear ≈ 87% M1 + 100% M2 + ≈40% M3. M4-M5 (пилот, AI-аватар, food scanner full) — пересобраны во внешний `Sprint B`, дедлайн 2026-07-15.
5. **Открытые блокеры**: T1 (152-ФЗ, open), T2 (LLM benchmark для русского, open), E1 (B2B→Consumer пивот, open), E2 (архитектура памяти, acknowledged).

---

## 0. Зачем этот документ

Без явного решения, какой репо канонизирует домены (user, booking, memory, payments), мы попадаем в одно из двух плохих состояний:
- **Дубли** в обоих репо → разная правда о пользователе, рассинхрон, конфликты YClients-зеркал, ad-hoc cron-сверки.
- **Произвольные миграции** между репо → потеря инвестиций в зрелые apps (`apps/conversations`, `apps/eventbus`, `apps/kb` уже есть в bot-platform, их перевозить дорого).

Этот документ:
- Описывает картину как есть, без интерпретаций.
- Формулирует архитектурный fork в три варианта.
- Объясняет, что меняется в каждом варианте и в MVP, и в фазах.
- Не делает решение за пользователя — выносит на обсуждение.

---

## 1. Что есть Ayla (продуктовая картина)

### 1.1 Позиционирование
*Источник: Brand Vision (status Proposal, 28.03), Product Vision v1.0 (30.03), PRD v3.0 (30.03).*

- **Слоган**: «AI, который помнит. Всегда.»
- **North Star**: «Стань лучшей версией себя — Ayla поможет каждый день.»
- **Голос**: подруга-эксперт. Тёплая, честная, действующая. Не холодный AI, не медицинское приложение.
- **Имя «Ayla»**: тюркское «лунный свет» + AI+la. Работает на трёх рынках (РФ / Казахстан / далее СНГ).
- **Аудитория**: женщины 20–45, городские, регулярные клиенты бьюти-услуг (расширенная аудитория после ребрендинга — было 22–40).
- **Языки**: ru / kk / en (kk-KZ полноценно — Phase 5).

### 1.2 Две поверхности (Two Apps)

| Surface | Bundle | Категория App Store | Кто |
|---|---|---|---|
| **Ayla** (Client) | `ru.ayla.client` | Health & Fitness | Конечный пользователь |
| **Ayla Pro** (Master) | `ru.ayla.pro` | Business | Мастер / небольшой кабинет |

**5 табов Ayla (DRF-116):** 🏠 Главная · 🍽️ Питание · ✨ Я · 📅 День · 👤 Профиль.
**4 таба Ayla Pro:** Расписание · Записи · Аналитика · Профиль.

### 1.3 Стратегический пивот 2026-05-19
*Источник: auto-memory `project_ayla_first_strategic_pivot`.*

«AI принадлежит пользователю, не салону. Один продукт Ayla + Ayla Pro. Ayla — главный бренд, салон — provider. Zero handoff customer UX + emergency system fallback. Voice deferred Phase 2+. Photo primary. Pilot pricing 0-590₽ + commission later. 9 fixed decisions.»

**Что это означает для архитектуры**: единый пользовательский контекст (cross-tenant memory) **не зависит от салона**. Салон — это provider знаний и расписания. Бот-канал (Telegram/MAX/WhatsApp) — четвёртая поверхность того же продукта. Один UserPersonalContext, общий для всех каналов.

### 1.4 Монетизация (по PRD §3, переопределена pricing pivot)
- **MVP по PRD**: 10% комиссия с записей через YooKassa.
- **Pivot 2026-05-19** (memory `project_pricing_model_hybrid`): пилот 0–590₽/мес + комиссия отложена. NOT 10% commission MVP. Founder-50 cohort валидирует retention.
- **Attribution machinery остаётся** для будущей комиссии (booking_source enum + ai_assist_score + billable + billing_reason).

### 1.5 Пилот
- **Город**: Пенза (PRD v2.0 переехал из Казани в Пензу, документ кое-где ещё содержит drift).
- **Цели по PRD**: 200+ мастеров за 3 месяца, 5000+ зарегистрированных клиентов, ≥3 food scans/day, ≥20% аватар-шеринг, D30 ≥15%, DAU/MAU ≥30%, NPS ≥50, LTV/CAC ≥9x.
- **Sprint B (Linear)**: пилот превратился в отдельный проект с дедлайном **2026-07-15** (DRF-300 launch checklist).

---

## 2. Что есть в коде

### 2.1 `ayla-ai-core` (PyCharm/ayla-ai-core)
*v0.8.1 production-ready, 221/221 тестов проходят, готовится к v1.0 freeze.*

- Pure Python library, zero Django. Optional `[django]` extra для ORM-store.
- `AIConcierge` orchestrator (450 LOC) — load history → render system prompt → call LLM → parse tool calls → return ChatResponseDTO.
- Provider adapters: OpenAI + Anthropic (claude-opus-4 / claude-sonnet через `AnthropicCompletionAdapter`).
- 5 tool definitions (show_specialists, show_slots, confirm_booking, show_my_bookings, ask_clarification) с anti-hallucination guard (валидация ID против `candidate_ids`).
- `BrandVoiceConfig` — multi-tenant параметризация (assistant name, business descriptor, off-topic redirect). Уже есть `FORMULA_TELA_VOICE` и `AYLA_MARKETPLACE_VOICE`.
- `PromptComposer` (v0.8.0) — fluent builder для не-booking доменов.
- Observability: `TenantContextFilter`, contextvars для `tenant_id`, `ReplayDeterminismError`.
- `ConversationStore` Protocol — consumer (бот или Ayla) реализует против своей ORM.
- Pinned в обоих consumer'ах через git+SHA.

**Verdict**: эта роль зафиксирована и работает. Никаких миграций сюда/отсюда. Просто продолжаем версионировать (v0.9.0 → v1.0.0 freeze).

### 2.2 `Ayla` (PyCharm/Ayla)
*Состоит из `frontAyla/` (Expo monorepo) + `djangoproject/` (Django backend).*

#### frontAyla (mobile monorepo, Yarn workspaces)
- `apps/client` — Expo приложение для клиентов (5 табов планируются).
- `apps/pro` — Expo приложение для мастеров (4 таба планируются).
- `packages/shared` — общий axios client, auth store, secure storage, UI components.
- **Реальное состояние**: master branch = BeautyGO-эра, 6 табов в client, 4 в pro, namespace `@beautygo/*` всё ещё активен. Ребрендинг в коде не выполнен.

#### djangoproject (Django 5.2 + DRF)
- 12 Django apps: `users`, `tenants` (новый), `appointments` (booking-движок DDD ✅), `services`, `reviews`, `nutrition` (food scanner scaffold), `ai` (пустой скелет), `analytics`, `notifications` (пустой), `payments`, `search`, `core`.
- **Booking-движок**: полноценный DDD (`appointments/domain/`, `application/`, `infrastructure/`). State machine, snapshot fields, idempotency keys, transactional outbox (worker не запущен), row-level locking (no-op на SQLite).
- **YooKassa**: two-stage hold→capture, webhook idempotency, комиссия 8% в коде vs 10% в PRD, split через `YOOKASSA_AGENT_ID`. **Payment модель живёт в `appointments/models.py:318`** — bounded context leak.
- **Auth**: OTP + JWT + Anonymous JWT (30d) + Social (VK/Google/Apple/Yandex) + `AppTypeMiddleware` (X-App-Type). DeviceToken с `app_type`.
- **БД**: SQLite в dev (блокер для concurrency/PostGIS/pgvector), `db.sqlite3` в git.
- **AI слой**: модели Conversation+Message — на feature-ветках, не в master.
- **UserPersonalContext**: не реализован.
- **Целая ветка** активной работы на feature-branches: food-scanner-slice-1/2/3-4, ai-chat-mvp, ai-conversation-models-drf-240, personal-context-infer-task-drf-230-pr2/pr3, cross-domain-bridge-drf-248, chat-service-aiconcierge-drf-241.
- **Целевой API**: `https://api.ayla.app/api/v1/`. Текущий dev: `https://dev.gobeauty.site/api/v1`.

### 2.3 `ai-bot-platform` (текущий репо)
*Активная разработка, 26 Django apps, multi-tenant с первого дня.*

| Apps | Что делает | Зрелость |
|---|---|---|
| `apps/tenancy` | Tenant model, slug, scoped managers, middleware с ContextVar | ✅ зрелый, STRICT_TENANT_SCOPE флипается Sprint 9 |
| `apps/identity` | BotUser (per-channel), ClientProfile (RFM/LTV скаффолд) | ✅ |
| `apps/conversations` | Conversation (FSM: IDLE/CONSULTING/ESCALATED/HUMAN_HANDOFF), Message | ✅ |
| `apps/orchestrator` + `apps/llm` | 7-hop pipeline, multi-provider routing, circuit breaker | ✅ |
| `apps/skills` (9 шт) | FAQ, booking, health, privacy, nutrition_anketa, food_logging, cancel/reschedule_booking, food_correction | ✅ |
| `apps/tools` | YClients calls, KB retrieval, slot resolver, reminder send | ✅ |
| `apps/booking` | BookingRequest, BookingReminder, PendingBookingAction — мирор YClients | ⚠️ дублирует Ayla domain |
| `apps/scheduling` | WorkingHours, ScheduleException, slot resolver — schema only | 🟡 partial |
| `apps/catalog` | CatalogService/Master/Category — 15-мин mirror из mysite | ⚠️ дублирует Ayla services |
| `apps/orders` + `apps/integrations/yookassa` | Order, PaymentEvent, hosted checkout, webhook | ⚠️ дублирует Ayla payments |
| `apps/channels` (max + telegram) | MAX Bot API + Telegram Bot API адаптеры | ✅ unique |
| `apps/ingress` + `apps/workers` | Redis Streams очередь, idempotent webhook enqueue | ✅ unique |
| `apps/kb` | KbDocument, ChromaDB per-tenant + system tenant `global_kb`, Google Docs seeder | ✅ unique |
| `apps/audit` | AuditLog immutable, cross-tenant leak scanner, PII redaction | ✅ unique |
| `apps/events` | Analytics envelope (snake_case, event_type/payload) | ✅ unique |
| `apps/eventbus` | DomainEvent (Postgres outbox, dot.notation, ULID, dispatcher) | ✅ unique |
| `apps/replay` | ReplayTrace, golden/adversarial fixtures | ✅ unique |
| `apps/consent` | ConsentRecord (152-ФЗ scaffold) | 🟡 |
| `apps/miniapp_api` + `apps/miniapp` | MAX Mini App: signed initData auth + REST + React shell | ✅ unique |
| `apps/handoff` | AdminTask, SLA tier logic (bronze/silver/gold) | ✅ unique |
| `apps/observability` | Shadow-mode diff dashboard, OpenTelemetry exporter | ✅ unique |
| `apps/promptreg` | Prompt registry с live-reload через Redis pub/sub | ✅ unique |
| `apps/experiments` | Sticky bucketing, A/B harness | ✅ unique |
| `apps/voice` | Voice rewriter (TTS prep), deferred Phase 6 | 🟡 |
| `apps/persona` | PersonaConfig (brand voice per tenant) | ✅ |
| `apps/adminconsole` (новый, untracked) | NotificationPreference для Settings Hub SH3 | 🟡 |
| `apps/loyalty`, `apps/promotions` | Scaffolding only | 🔴 deferred |

**Доп. факты:**
- Python 3.12, Django 5.2, **uv** package manager (не pip/poetry).
- 205 test files, pytest+model-bakery+freezegun, ruff+mypy+pre-commit, GitHub Actions.
- 55 .md docs (7 ADRs, 11 UX handoffs, 15 design policies, decisions-log r20, sprint plans, runbooks).
- ChromaDB 0.5, Redis 7, Postgres 16 в docker-compose.yml.
- `ayla-ai-core` pinned `v0.8.1 @ git+ssh://...@<sha>`.

### 2.4 Где что живёт сегодня (матрица)

| Домен | ayla-ai-core | Ayla djangoproject | ai-bot-platform |
|---|:---:|:---:|:---:|
| AI orchestration / brand voice | ✅ canonical | (consumer) | (consumer) |
| LLM provider adapters | ✅ | — | overrides in `apps/llm` |
| User auth (OTP + JWT + social) | — | ✅ | (consumes Ayla) |
| User identity / profile | — | ✅ User+Client+SpecialistProfile | BotUser (per-channel wrapper) + ClientProfile (RFM) |
| Booking domain | — | ✅ Appointment DDD | ⚠️ дубль (BookingRequest mirror) |
| Payments (YooKassa) | — | ✅ (но в appointments/) | ⚠️ дубль (`apps/orders`) |
| Catalog (services/masters) | — | ✅ Service + ServiceTemplate | ⚠️ дубль (`apps/catalog` mirror) |
| Tenancy multi-tenant | — | 🟡 tenants app (новый) | ✅ зрелый |
| Conversation/Message | — | feature-branches | ✅ canonical |
| Skills/Tools FSM | — | — | ✅ canonical |
| KB / RAG / ChromaDB | — | — | ✅ canonical |
| Audit / Events / Eventbus / Replay | — | — | ✅ canonical |
| MAX / Telegram channels | — | — | ✅ canonical |
| MAX Mini App | — | — | ✅ canonical |
| Observability / shadow-mode | — | — | ✅ canonical |
| UserPersonalContext (память) | — | spec (Notion) | not started |
| Food Scanner | — | nutrition app scaffold | `apps/skills/food_logging` (бот-side) |
| AI-аватар | — | — | — (deferred Phase 2 per DRF-235) |
| Voice (STT+TTS) | — | — | `apps/voice` scaffold |
| Mobile (Expo) | — | ✅ frontAyla | — |

### 2.5 Linear-прогресс (на 2026-05-20)
*Источник: проект Ayla в команде DRF.*

- **M1 (Auth & Foundation)** — deadline 06.04 — Done ≈ **87%**. Висит DRF-116 (BottomNav design, Urgent, Todo).
- **M2 (Catalog & Discovery)** — deadline 19.04 — Done ≈ **100%** (по выборке).
- **M3 (Booking & Payments)** — deadline 10.05 — частично. AI Chat REST endpoints (DRF-104) Done. **DRF-230 UserPersonalContext In Progress, due 2026-06-15** — load-bearing для North Star.
- **M4 (AI + Home Screen)** — deadline 31.05 — пересобран в `Sprint B — Production Validation`, новый дедлайн 2026-07-15.
- **M5 (Pilot Penza)** — deadline 30.06 — Food Scanner UI и Avatar UI Done; AvatarService BE отложен в Phase 2 решением DRF-235 после PM-аудита 27.04.
- **Phase A (ayla-ai-core extraction)** — Done полностью (DRF-236..240, 244, killer-scenario DRF-248).
- **Multi-tenant scoping Phase A.7** — Done (JWT с tenant_id, OpenAPI X-Tenant header, composite indexes, seed default tenants).
- **Текущий активный гейт**: DRF-891 dev-bot flow + branch protection — блокирует X-5% canary ramp в bot-platform.

---

## 3. Стратегическое противоречие, выявленное аудитом

Notion (март 2026) описывает Ayla так: «два мобильных приложения, единый Django backend (`djangoproject/`), Claude Sonnet + MCP Server». Бот-канал не упоминается вообще.

Реальный код (май 2026) выглядит так: **самый зрелый сервер находится в `ai-bot-platform`**, не в `djangoproject/`. Он уже multi-tenant, уже с RAG, уже с observability, уже с conversations и skills, уже с MAX Mini App.

Получаются три возможных интерпретации, что **должно** быть Ayla:

| Интерпретация | Ayla djangoproject | ai-bot-platform | Mobile App ходит куда |
|---|---|---|---|
| **Вариант A** (рекомендуемый) | Booking + Payments REST для mobile | AI-бэкбон (conversations, memory, RAG, channels, observability) | в оба (split-domain mobile API) |
| **Вариант B** | Не нужен как самостоятельный сервис (Expo-фронт остаётся) | Всё-в-одном: user + booking + payments + AI + channels | только в ai-bot-platform |
| **Вариант C** | Всё-в-одном (по Notion): user + booking + payments + AI + memory | Тонкий канальный слой (MAX/Telegram), вызывает Ayla REST | только в Ayla djangoproject |

---

## 4. Три варианта подробно

### Вариант A — bot-platform = бэкбон, Ayla djangoproject = REST для booking/payments

**Идея**: каждый репо специализируется на том, в чём он уже силён. `ai-bot-platform` канонизирует user/memory/conversation/RAG/tenancy/observability/channels. `Ayla djangoproject` — узкий сервис booking-движка (DDD + YooKassa + YClients sync) + REST API для Expo-приложений.

**Топология:**

```
                ┌─────────────────────────────────┐
                │  ayla-ai-core (lib, v0.8.1)     │
                └────────┬─────────────────┬──────┘
                         │                 │
   ┌─────────────────────▼──────────┐    ┌─▼──────────────────────────────┐
   │  ai-bot-platform               │    │  Ayla djangoproject            │
   │  (AI/Memory/Channels backbone) │    │  (Booking + Payments REST)     │
   │                                │    │                                │
   │  • User identity + memory      │◄───┤  • Appointment DDD             │
   │  • Conversation / Message      │    │  • YooKassa                    │
   │  • Skills + Tools              │    │  • YClients sync               │
   │  • KB / RAG / ChromaDB         │    │  • REST for Expo apps          │
   │  • MAX/Telegram channels       │    │  • Catalog (mirror→shared)     │
   │  • MAX Mini App                │    │                                │
   │  • Tenancy + Audit + Eventbus  │    │                                │
   │  • Observability               │    │                                │
   │                                │    │                                │
   └────────────┬───────────────────┘    └────────────┬───────────────────┘
                │                                     │
                │ (REST + DomainEvents subscribe)     │
                │                                     │
       ┌────────┴─────────┐                  ┌────────┴─────────┐
       │  Telegram + MAX  │                  │  Expo: Ayla +    │
       │  (messenger UX)  │                  │  Ayla Pro mobile │
       └──────────────────┘                  └──────────────────┘
```

**Что мигрирует:**
- `ai-bot-platform/apps/orders/` → консолидируется с `Ayla djangoproject/payments/` (один YooKassa-слой).
- `ai-bot-platform/apps/catalog/` → остаётся только как кеш-слой для бота, master данные читаются из Ayla REST.
- `ai-bot-platform/apps/booking/` → сокращается до bot-only state machine (reminder escalation, pending action), модели становятся proxy/cache.
- `Ayla djangoproject/users` → передаёт `tenant_id` в JWT для `ai-bot-platform` и `ayla-ai-core`.
- **UserPersonalContext (DRF-230)** → переезжает или начинает строиться в `ai-bot-platform/apps/identity/` (там уже ClientProfile + RFM). См. §5.

**Mobile API:**
- Expo-приложения ходят в **оба** репо:
  - Ayla djangoproject: `/api/v1/auth/`, `/users/`, `/appointments/`, `/services/`, `/specialists/`, `/payments/`, `/reviews/`.
  - ai-bot-platform: `/api/v1/customer/auth/verify` (уже есть), `/api/v1/customer/chat/`, `/api/v1/customer/memory/`, `/api/v1/customer/conversations/`.
- Через единый API Gateway (Nginx) с маршрутизацией по path.

**Аргументы за:**
- Минимум миграций кода. Не ломаем зрелые apps `conversations/eventbus/kb/audit/replay/observability` в bot-platform.
- Booking DDD в Ayla djangoproject уже работает — оставляем как есть.
- Каждый репо сохраняет свой источник правды (booking → Ayla, memory → bot).
- Channels естественно остаются в bot-platform, REST для mobile — в Ayla djangoproject (исторически правильно).
- Решает дублирование YooKassa: уходит из bot-platform, остаётся в Ayla.

**Аргументы против:**
- Mobile теперь ходит в два сервиса — больше сложность во фронте, два axios-клиента, два JWT-контракта (хотя `tenant_id` claim общий).
- Cross-service consistency: запись в Ayla → событие → bot-platform обновляет память. Eventbus pattern уже есть в bot-platform — придётся добавить consumer на стороне Ayla djangoproject или REST polling.
- Catalog: salon добавил услугу в YClients → нужно решить, кто канонично её хранит. В варианте A — Ayla djangoproject (потому что Expo тоже её показывает в каталоге мастеров).

**Риски:**
- Drift между двумя User-моделями (Ayla User vs bot BotUser+ClientProfile). Нужен strict JWT contract + соглашение «Ayla owns user PII, bot owns channel+RFM».
- YClients webhook сейчас приходит в bot-platform (`/api/v1/yclients/webhook/`) — нужно либо переадресовать в Ayla, либо bot-platform пересылает событие через eventbus.

### Вариант B — bot-platform = всё-в-одном, Ayla djangoproject уходит

**Идея**: оставляем один Django-сервер. Переносим booking DDD + YooKassa + REST для mobile из `Ayla djangoproject` в `ai-bot-platform`. Ayla djangoproject «увядает», Expo-фронт остаётся в `Ayla/frontAyla/`, но смотрит уже только в ai-bot-platform.

**Топология**: один Django-репо обслуживает все 4 поверхности (Expo client, Expo pro, MAX, Telegram).

**Что мигрирует:**
- `Ayla/djangoproject/appointments/` (booking DDD) → `ai-bot-platform/apps/booking/` (поверх существующего apps/booking + расширение моделей).
- `Ayla/djangoproject/users/` + auth → `ai-bot-platform/apps/identity/` (расширение).
- `Ayla/djangoproject/payments/` + YooKassa → консолидация с `apps/orders/` + `apps/integrations/yookassa/`.
- `Ayla/djangoproject/services/`, `reviews/`, `nutrition/` → новые apps в ai-bot-platform.
- `Ayla/djangoproject/` репозиторий со временем заархивирован.

**Аргументы за:**
- Один Django-сервер — один deploy, один монитор, один lock-файл, один Postgres, одна Celery, одна Sentry.
- Нет cross-service consistency проблемы.
- Используем готовую multi-tenancy и observability bot-platform для всего.
- Mobile фронт ходит в один API.

**Аргументы против:**
- **Очень большой объём миграций**: переносим зрелый booking DDD (~3000 LOC), 12 апп, 22 тест-файла из Ayla djangoproject. Большой риск регрессий.
- Linear-прогресс M1/M2 был сделан в Ayla djangoproject — переписываем то, что уже работает.
- Phase A.7 multi-tenant scoping (DRF-242.x) был на стороне Ayla djangoproject (JWT tenant_id, X-Tenant header). Дублирование уже было сделано — будет двойная работа.
- API контракт `api.ayla.app/api/v1/` в Notion API Spec v2.0 описывает 60+ endpoints — все эти роуты придётся перенести.

**Риски:**
- Сроки сдвигаются на 4-6 недель минимум. Пилот в Пензе (Sprint B, 2026-07-15) уже сжатые.
- Команда Ayla, работающая на feature-ветках food-scanner-*, ai-conversation-models-drf-240, personal-context-drf-230 — их работа смержится не туда.

### Вариант C — Ayla djangoproject = всё-в-одном, bot-platform = тонкий канал

**Идея**: следуем Notion архитектуре буквально. `Ayla djangoproject` канонизирует всё (user + booking + payments + memory + RAG + AI orchestration). `ai-bot-platform` сжимается до channel layer: получает webhook от MAX/Telegram → парсит → вызывает Ayla REST API → форматирует ответ.

**Что мигрирует:**
- `ai-bot-platform/apps/conversations/` → `Ayla djangoproject/conversations/` (новая app).
- `ai-bot-platform/apps/orchestrator/` + `llm/` + `skills/` + `tools/` → `Ayla djangoproject/ai/` (расширение).
- `ai-bot-platform/apps/kb/` + ChromaDB → `Ayla djangoproject/kb/` (новая app).
- `ai-bot-platform/apps/eventbus/` + `events/` + `audit/` + `replay/` + `observability/` → `Ayla djangoproject/` (5 новых апп).
- `ai-bot-platform/apps/tenancy/` → возможно зачем-то остаётся в обоих или мигрирует в Ayla.
- `ai-bot-platform/apps/identity/` (BotUser, ClientProfile) → объединяется с Ayla User.
- `ai-bot-platform/apps/persona/`, `promptreg/`, `experiments/`, `consent/`, `handoff/` → все мигрируют.
- `ai-bot-platform/apps/miniapp_api/` + `miniapp/` → переезжают в Ayla.
- В bot-platform остаются: `apps/channels/`, `apps/ingress/`, `apps/workers/` + тонкие caller'ы Ayla REST.

**Аргументы за:**
- Соответствует Notion-документации (которая является source of truth для product/design).
- Один источник правды о пользователе (memory не fragmented между двумя сервисами).
- Концептуально чисто: Ayla = product, bot = transport.

**Аргументы против:**
- **Огромный объём миграций**: переносим 15+ зрелых апп. ~10-20 тысяч LOC переезжают. 11 UX handoffs + 15 design policies + 7 ADRs ai-bot-platform либо переезжают, либо устаревают.
- ChromaDB per-tenant + global fallback в Ayla djangoproject придётся подсоединять заново.
- Phase A multi-tenancy и Sprint 9 STRICT_TENANT_SCOPE — переделываются.
- Sprint B canary ramp (5% → 100% MAX) сейчас активен в bot-platform — он остановится.
- В Ayla djangoproject SQLite в dev, нет Celery+Redis в коде settings — нужна инфра-миграция.
- Команда bot-platform теряет 90% своего работы продуктово.

**Риски:**
- 6-12 недель миграций. Пилот в Пензе сдвигается.
- Воркеры, observability, replay в Ayla — это всё нужно поднимать. Ayla djangoproject не имеет таких практик в репо.

---

## 5. Открытые вопросы, блокирующие финальный выбор

### 5.1 Где живёт `UserPersonalContext` (память Ayla)?
*Linked task: Task #3, отложен пользователем для углубления.*

Три подварианта (независимы от A/B/C):

**5.1.1 Гибрид (рекомендуется по простоте под A):**
- Cross-channel память (workplace_district, preferred_time, diet, life_events) → в `ai-bot-platform/apps/identity/UserPersonalContext`. Доступна через REST и через core-prompt-builder.
- Provider-specific память (история визитов в салон X, отзывы) → остаётся per-tenant в booking-модели Ayla djangoproject.
- Граница: core-память **никогда не пересекает tenant boundary**, provider-память — никогда не покидает provider.

**5.1.2 Отдельный memory-сервис (`ayla-memory`):**
- Самостоятельный Postgres, REST/gRPC, оба consumer'а (Ayla + bot) ходят туда.
- Дороже инфры на ~$20-40/мес. Дороже cognitive overhead.
- Чище разделение, но overkill для MVP.

**5.1.3 Канонично в одном из репо (под B → bot, под C → Ayla):**
- Под вариантом B память в bot-platform автоматически, потому что там всё.
- Под вариантом C — в Ayla, как описано в Notion AI-Personalization doc.

### 5.2 Booking system-of-record: YClients vs локальная база
*Текущая правда*: YClients = SoR для салонов с YClients-аккаунтом. Локальная база = SoR для самозанятых мастеров без YClients (через Ayla Pro app). И то, и другое поддерживается. Нужно явно зафиксировать.

### 5.3 Catalog ownership при варианте A
- Если salon = provider и его услуги — это provider-знание, тогда catalog принадлежит per-tenant в bot-platform (где tenancy зрелый).
- Если catalog — это «что Ayla показывает пользователю», тогда он в Ayla djangoproject.
- Возможно: bot-platform mirrors YClients per-tenant, Ayla djangoproject читает из bot-platform через REST или через shared DB view.

### 5.4 Mobile API split (под A)
- Когда Expo-приложение делает action «AI находит и записывает мастера в один тап» — это:
  - один вызов в bot-platform `/api/v1/customer/chat/` (LLM intent + tool_call create_booking)?
  - bot-platform внутри идёт в Ayla djangoproject booking API?
  - или Expo идёт сначала в bot-platform за интентом, потом сам в Ayla за записью?
- Решение влияет на UX latency и на failure modes.

---

## 6. Моя рекомендация

**Вариант A — split-domain с явными контрактами.**

Аргументы:
1. **Минимум разрушения уже сделанного.** M1+M2 в Ayla djangoproject (87% + 100%) и Sprint 9 / Phase A.7 в bot-platform — обе работы остаются в своих репо.
2. **Sprint B (пилот Пенза, deadline 2026-07-15) не сдвигается**, потому что canary в bot-platform продолжается, booking REST в Ayla — продолжается.
3. **Память (UserPersonalContext) естественно ложится в bot-platform**, потому что там уже есть Conversation, identity, ClientProfile, observability, audit, replay — всё нужное для построения и аудита памяти.
4. **Catalog не дублируется по-настоящему**: bot-platform держит provider-specific mirror per-tenant (что и сейчас), Ayla djangoproject получает только canonical услуги и мастеров через REST.
5. **YooKassa уезжает из bot-platform** (`apps/orders` упрощается до состояния «display order in chat»). Это убирает реальный конфликт.
6. **Mobile split API** — нормальная практика (вспомните Uber: Driver и Rider ходят в разные backend-сервисы). Решается единым API Gateway.

**Что нужно решить сразу при выборе A:**
- Вопрос 5.1 (память): я предлагаю **гибрид (5.1.1)** — память в bot-platform + provider-specific в Ayla. Это согласовано с Ayla-first pivot.
- Вопрос 5.2 (booking SoR): сохраняем текущее — YClients для тех, у кого есть; Ayla djangoproject для остальных.
- Вопрос 5.3 (catalog): bot-platform держит per-tenant mirror, Ayla djangoproject читает via REST.
- Вопрос 5.4 (mobile API): Expo обращается сначала в bot-platform за интентом и slot recommendations, bot-platform делает internal call в Ayla djangoproject для собственно `create_booking`.

---

## 7. MVP (под вариантом A)

### 7.1 Что включает MVP (P0 stories из PRD v3.0)

12 P0 user stories из PRD v3.0 (DEFINITIVE):

| Код | Story | Где живёт | Linear status |
|---|---|---|---|
| C-01 | AI-чат: «какую услугу когда хочу» → подборка мастеров со слотами | bot-platform (intent) + Ayla (slots) | M3 partial |
| C-02 | Выбрать мастера и слот → подтверждение в 1 tap | Ayla booking API | M1 Done |
| C-03 | Видеть предстоящие записи на главном экране | Ayla REST | M1 Done |
| C-04 | Регистрация по номеру телефона (OTP) | Ayla auth | M1 Done |
| C-05 | Отменить или перенести запись | Ayla + reminder state machine bot | M1 Done |
| M-01 | Мастер создаёт профиль с услугами/ценами/длительностью | Ayla Pro / Ayla services | M1 partial (200 DRF) |
| M-02 | Мастер управляет расписанием | Ayla scheduling | M1 partial |
| M-03 | Мастер получает уведомления о новых записях | bot-platform push (FCM/APNs) | M2 Done (DRF-140) |
| M-04 | Мастер видит свои записи | Ayla Pro REST | M1 Done |
| S-01 | AI понимает intent (тип услуги, время, локация, бюджет) | bot-platform orchestrator + ayla-ai-core | M3 (DRF-104 Done, S-01 acceptance ≠ done yet) |
| S-02 | Ranking мастеров по доступности/расстоянию/рейтингу/цене | bot-platform (skills/tools/booking) | M3 partial |
| C-13 | Онлайн-оплата YooKassa с комиссией 10% (перемещён из P2) | Ayla payments | M2 Done (DRF-133) |

### 7.2 Что включает MVP сверх P0 (по решению Notion §3 Non-Goals):
- **AI Food Scanner** (Phase 1 / M4 по PRD timeline) — `apps/skills/food_logging` в bot-platform уже частично работает, ещё нужен Ayla nutrition module для Expo и LogMeal/Passio интеграция.
- **AI-аватар** — **DEFERRED** в Phase 2 (DRF-235 decision).
- **Голосовой режим (STT+TTS)** — в PRD MUST для MVP, но в `apps/voice` scaffold. Реальная работа Phase 6 / Sprint 13-14.
- **UserPersonalContext (DRF-230)** — In Progress в Linear, load-bearing.
- **Пилот Пенза (Sprint B)** — DRF-300 launch checklist, deadline 2026-07-15.

### 7.3 Реалистичный MVP-0 (под A)
*Без AI-аватара, без full voice — то, что реально к pilot launch 2026-07-15.*

1. Закрыть DRF-116 (Bottom Navigation design).
2. Закончить DRF-230 (UserPersonalContext в bot-platform — см. §8 на смысл).
3. Закончить DRF-241 (REST /api/v1/ai/chat/ over AIConcierge).
4. Завершить consolidation: убрать `ai-bot-platform/apps/orders` или превратить в display-only.
5. Catalog: bot-platform mirror remains; Ayla djangoproject читает via REST (новый client).
6. Mobile API split: Expo подключает второй axios-клиент в bot-platform.
7. Ребрендинг BeautyGO → Ayla (Bundle IDs, package namespace, repo rename — Open question #7 в audit).
8. Sprint B pilot validation (DRF-231, DRF-232, DRF-233, DRF-234) — H1 food scan, H3 memory positioning, Decision Day.

---

## 8. Фазы (под рекомендованным A)

### Phase 0 — Decision & Foundation (2-3 недели, до 2026-06-10)
- Решение по варианту A/B/C — **этим документом**.
- Решение по 5.1 (memory home) — гибрид рекомендован.
- Ребрендинг BeautyGO → Ayla во всех 3 репо (package names, repo names, Bundle IDs, App Store Connect).
- Миграция Ayla djangoproject dev на Postgres (вместо SQLite) + Celery + Redis.
- Снятие `.mcp.json` с plaintext-токенами из репо (gitignore, vault).
- API Gateway (Nginx) для split-domain mobile API.
- Закрыть DRF-116, DRF-891 (текущий active gate).

### Phase 1 — MVP к пилоту Пензы (4-6 недель, до 2026-07-15)
- Завершить DRF-230 UserPersonalContext в bot-platform.
- Завершить DRF-241 AI Chat REST endpoints.
- Очистить YooKassa-дублирование (one source).
- Catalog REST между репо.
- Food Scanner (Logmeal/Passio + Ayla nutrition + bot skill).
- Sprint B pilot validation (DRF-231..234) → Decision Day → M3 roadmap update.
- Pre-launch QA (DRF-141 готов; нужны новые QA по food scanner + memory).
- 50 мастеров онбординг в Пензе.

### Phase 1.5 — Personalization v2 + Pro app polish (~2-3 недели, конец июля)
- Расширенный UserPersonalContext: 8 anti-spam rules в проде, 3 источника данных (explicit/behavioral/signals).
- Celery `infer_user_patterns` daily + `cleanup_sensitive_data` weekly (152-ФЗ).
- Settings Hub UI (NotificationPreference в `apps/adminconsole`).
- Conversation Dashboard для админов салонов.
- Schedule Management UX (TimeBlock, manual booking, conflict resolution).

### Phase 2 — Scale + AI-аватар + Voice (август-сентябрь)
- AI-аватар (DRF-147 AvatarService) с Ready Player Me — recheck решение Phase 2.
- Voice STT+TTS в проде (Yandex SpeechKit primary, ElevenLabs fallback).
- Multi-region инфра (RU prod stable, KZ начинаем).
- LLM benchmark T2 closure: финальный выбор LLM для русского.
- 152-ФЗ legal audit closure (T1).
- E1 (B2B→Consumer expertise gap) → найм/менторство.

### Phase 5 — Казахстан (октябрь-ноябрь)
- Локализация ru → kk + en.
- Kaspi Pay интеграция (вместо/вдобавок к YooKassa).
- 50 мастеров в Алматы.
- Инвест-встречи (по PRD §3 Goal 5).

---

## 9. Dev / Prod схема (под A)

### 9.1 ai-bot-platform
- **Dev**: текущий docker-compose.yml (Postgres 16, Redis 7, ChromaDB 0.5, MinIO, Django dev server). Dev URL: TBD (сейчас не зафиксирован — есть `dev-bot flow` DRF-891).
- **Prod**: Kubernetes namespace `ai-bot-platform-prod`, Sentry env=production, отдельные YooKassa shop_id (prod), отдельные MAX/Telegram bot tokens per-tenant.
- **Branches**: `main` = prod, `dev` = staging, feature branches → PR → review → merge dev → canary 5%/25%/50%/100% → merge main.

### 9.2 Ayla djangoproject
- **Dev**: миграция с SQLite на Postgres 16 (docker-compose), Celery+Redis, отдельный YooKassa test-shop. Dev URL: `dev.ayla.app` (переименовать с `dev.gobeauty.site`).
- **Prod**: `api.ayla.app` (по Notion API spec). Отдельный VPS / K8s namespace `ayla-djangoproject-prod`.
- **Branches**: то же что bot-platform — `main`/`dev`.

### 9.3 ayla-ai-core
- **Не имеет окружений** — это library. Релизы через git tags (v0.8.1 → v0.9.0 → v1.0.0). Consumer'ы pin'ят SHA в pyproject.toml.

### 9.4 Mobile (frontAyla)
- **Dev build**: EAS Build dev channel, `EXPO_PUBLIC_API_BASE_URL=https://dev.ayla.app/api/v1`, `EXPO_PUBLIC_BOT_API_BASE_URL=https://dev-bot.ayla.app/api/v1`.
- **Prod build**: EAS Build prod channel, prod URLs.
- **App Store/TestFlight**: bundle `ru.ayla.client`, `ru.ayla.pro`. До запуска в App Store — внутренний distribution.

### 9.5 Shared infrastructure
- **Postgres**: два разных DB instance (`ai-bot-platform-prod`, `ayla-djangoproject-prod`). Не делим один Postgres для безопасности.
- **Redis**: один кластер на все три consumer'а (namespace prefixes).
- **ChromaDB**: только в ai-bot-platform (per-tenant + global_kb).
- **S3 / MinIO**: один bucket per repo (`ayla-bot-platform-prod`, `ayla-djangoproject-prod`).
- **Sentry**: отдельные projects на каждый сервис.
- **OpenTelemetry collector**: shared, экспорт в Sentry/vendor.

### 9.6 Secrets
- **Vault** (HashiCorp Vault или 1Password Connect) для prod-секретов (YooKassa shop_key, JWT signing key, OpenAI/Anthropic keys, SMS.RU, MAX bot tokens, Telegram tokens, Firebase service account).
- **.env.example** в каждом репо без значений.
- **.mcp.json** убрать из репо (plaintext Figma+Notion токены).
- Pre-commit `detect-secrets` уже работает в bot-platform — добавить в Ayla djangoproject.

---

## 10. Что мигрирует, что нет (детально, под A)

### 10.1 Из ai-bot-platform — что упрощается или мигрирует в Ayla djangoproject

| Файл / app | Что делать | Аргумент |
|---|---|---|
| `apps/orders/` | Превратить в display-only (рендер заказа в чате); YooKassa lifecycle живёт в Ayla payments | Один YooKassa-слой; bot не должен держать деньги |
| `apps/integrations/yookassa/` | Уходит в Ayla, в bot остаётся тонкий REST-клиент к Ayla payments | Same |
| `apps/catalog/` | Остаётся как per-tenant mirror, но read-only из Ayla djangoproject через REST для canonical услуг | Catalog = provider knowledge, не Ayla user data |
| `apps/booking/BookingRequest` | Превращается в RemoteBookingProxy (cache локально для FSM reminders/escalation), canonical в Ayla | YClients = SoR + Ayla = user-side mirror |
| `apps/integrations/ayla/` | Расширяется до полноценного Ayla REST client | Зрелая граница между сервисами |

### 10.2 Из Ayla djangoproject — что остаётся, что чистится

| Что | Что делать | Аргумент |
|---|---|---|
| `appointments/domain/` DDD | Остаётся, source-of-truth booking | Уже работает, M1 Done |
| `appointments/models.py::Payment` (line 318) | Переезжает в `payments/models.py` | Bounded context fix (audit §3.5) |
| `users/` | Остаётся canonical | Source of user truth |
| `tenants/` | Используется как provider FK, синхронизируется с bot-platform tenancy | Один tenant-вселенная |
| `nutrition/` (food scanner scaffold) | Расширяется до полноценного food domain, кооперируется с `bot-platform/apps/skills/food_logging` | Food = mobile primary, bot — secondary |
| `ai/` (пустой scaffold) | Удаляется; AI живёт в bot-platform | Чистка |
| `analytics/`, `notifications/` (пустые скелеты) | Используются как тонкие REST-fronts; реальная аналитика и push — в bot-platform | Чистка |
| `search/` (вне INSTALLED_APPS) | Регистрируется правильно или удаляется | Audit §3.1 fix |
| `db.sqlite3` в git | Удаляется, добавляется в .gitignore | Audit §7.4 fix |

### 10.3 Что НЕ мигрирует
- `ai-bot-platform/apps/conversations/`, `orchestrator/`, `llm/`, `skills/`, `tools/`, `kb/`, `audit/`, `events/`, `eventbus/`, `replay/`, `tenancy/`, `observability/`, `consent/`, `handoff/`, `promptreg/`, `experiments/`, `voice/`, `persona/`, `miniapp_api/`, `miniapp/`, `channels/`, `ingress/`, `workers/`, `adminconsole/`.

Все они остаются в bot-platform. Это и есть AI-бэкбон Ayla.

---

## 11. Риски и открытые вопросы

### 11.1 Из PRD Risk Register (всё ещё open)
| ID | Риск | Тип | Статус | Mitigation |
|---|---|---|---|---|
| **T1** | 152-ФЗ для UserPersonalContext (особенно красная зона) | Launch-blocking | 🔴 Open | Юр.аудит до старта Phase 1 |
| **T2** | Качество LLM для русского | Launch-blocking | 🔴 Open | Бенчмарк уже частично сделан (Linear: «LLM Benchmark — Выводы и рекомендация» документ) — закрыть до Phase 1 |
| **E1** | B2B→Consumer пивот expertise gap | Elephant | 🔴 Open | Нанять growth lead / ментор |
| **E2** | Архитектура памяти | Elephant | 🟠 Acknowledged | Этот документ + Task #3 |

### 11.2 Из Architecture Review (audit 23.04, всё ещё open)
- **9 критичных пробелов** в Ayla djangoproject — 5 из них закрываются миграцией на Postgres+Celery+Redis (Phase 0).
- **8 структурных отклонений** — частично закрываются Phase 0/1 (Payment-in-Appointments, search вне INSTALLED_APPS, settings/test.py).

### 11.3 Из аудита ai-bot-platform
- **Highest-Risk Gap #1** (tech-lead-control-plane.md): documentation drift — README + phase-1-kickoff.md описывают старое состояние.
- **#2**: Schedule MVP gap — TimeBlock, ScheduleChangeRequest, manual booking ещё спеки.
- **#3**: Slot occupancy precision (customer/slots) использует reminder rows.
- **#4**: Admin web/API surface — handoff specs (`/api/v1/settings`, `/api/v1/conversations`, `/api/v1/analytics`, `/api/v1/masters`) ещё specs only.

### 11.4 Notion-vs-code drift
PRD v3.0 содержит несколько противоречий:
- «Voice входит в MVP» (Non-Goals) vs «C-11 voice — P2» (user stories).
- «10% commission MVP» (PRD) vs «pricing pivot 2026-05-19» (memory: 0-590₽).
- «Пенза» (текущий пилот) vs «Казань» (старая версия PRD местами).
- «Bundle IDs ru.ayla.*» (PRD v3.0) vs «ru.beautygo.*» в коде (frontAyla).

---

## 12. Что нужно от пользователя

После прочтения этого документа я прошу решений по:

1. **Архитектурный вариант**: A / B / C (моя рекомендация — A).
2. **Memory home (5.1)**: гибрид / отдельный сервис / каноничный в одном репо.
3. **Catalog ownership (5.3)** при варианте A.
4. **Mobile API contract (5.4)** при варианте A.
5. **Приоритет ребрендинга (open question #7 из audit)**: сейчас в Phase 0 или после пилота?

После этих 5 решений я могу:
- Зафиксировать решения в auto-memory (`project_ayla_architecture_decision`).
- Написать конкретный Sprint plan на Phase 0 (3-4 спринта по 1 неделе).
- Подготовить миграции по §10 (orders → Ayla, catalog REST contract, BookingRequest → RemoteBookingProxy).
- Запустить очистку Ayla djangoproject (Payment refactor, search registration, SQLite→Postgres, db.sqlite3 cleanup).

---

## 13. Что я НЕ сделал в этом документе

- Не аудировал docs/design/handoffs/ и docs/design/policies/ в bot-platform детально (11 + 15 файлов). Они нужны для Phase 1 sprint planning, но не для архитектурного решения.
- Не предложил конкретные API контракты между Ayla djangoproject и ai-bot-platform — это работа Phase 0 sprint planning после выбора варианта.
- Не оценил инфра-cost под три варианта в долларах — нужно если решающий фактор.
- Не пересмотрел 17 P1/P2 user stories из PRD под выбранный архитектурный вариант — это работа в Phase 1.

---

*Документ написан по запросу пользователя «надо разобрать все документы Notion Ayla namespace, свести воедино концепцию и выстроить разработку MVP и далее по фазам». Все факты сверены с Notion (7 документов, все таблицы загружены), Linear (проект Ayla + 8 связанных проектов), и кодом всех трёх репозиториев. Никаких выдумок — если в источнике нет, в документе помечено как open question или TBD.*
