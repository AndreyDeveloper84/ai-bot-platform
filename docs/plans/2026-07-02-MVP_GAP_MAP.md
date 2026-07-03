# MVP_GAP_MAP_2026-07 — v1.2

> **v1.2 (2026-07-02):** 2-е ревью founder — Stream 4 → «Pilot Discovery Ranking» (защита scope); `review_count` вынесен в тикет **#1060** (S4.0, linked #1044); Freeze rule дополнен правилом New-Scope-SP; PR sequencing/волны разбиты на 1A/1B/1C (S0-A разгружен, S0-B = единственное место правки прочих клиентов, S0-C после A/B, S1 = epic 3–4 PR).
> **v1.1 (2026-07-02):** ревью founder — Stream 0 → A/B/C, `event_id` → Stream 0.5, safety раньше booking, Freeze rule/PR sequencing/волны/DoD, номера тикетов. Сверено с роадмапом (PR #1015, G1–G10) и GitHub.
> **Составлено:** 2026-07-02 по фактическому коду трёх репозиториев (не по Repomix).
> **Метод:** 6 параллельных read-only аудитов доменов + перекрёстная сверка находок.
> **Репозитории:**
> - `C:\Users\user\PycharmProjects\ai-bot-platform` (ветка dev) — AI/MAX runtime
> - `C:\Users\user\PycharmProjects\Ayla\djangoproject` — canonical transaction backend
> - `C:\Users\user\PycharmProjects\ayla-ai-core` — shared AI library (пин в bot: `git@e73a1b4`)
>
> Все ссылки формата `path:line` относятся к соответствующему репозиторию домена.

---

## 0. Executive summary (для founder / tech-lead)

Картина подтвердила тезис брифинга: **это не старт MVP, а почти готовая двух-backend система**, где риск не «ничего нет», а «много готово, но стыки контрактов кривые и половина мостов выключена/не заполнена».

Три вывода, меняющие приоритеты:

1. **Живой пилотный путь сегодня НЕ соответствует ADR-0009 и НЕ проходит через safety/consent.** Глобальный (пилотный) MAX-бот идёт мимо реестра скиллов — без consent-гейта и safety-слоя. Booking по умолчанию пишет **локальную** canonical-запись (`BOOKING_VIA_AYLA_REST=false`). Обе вещи — осознанные (память founder), но означают: заявление «делегируем в Ayla + гейтим согласие» **code-complete, но не активно**.

2. **Контракты между репо расходятся сильнее, чем ожидалось.** Из 5 REST-клиентов bot→Ayla **только `booking_client` совпадает** с реальным API Ayla. `profile_client` и `recommendations_client` сломаны дважды каждый (путь + токен). Секрет `AYLA_SERVICE_TOKEN`, на который завязаны 3 клиента, **на стороне Ayla не существует**.

3. **Cross-service мосты выключены или «мёртвые».** Внешняя доставка событий double-gated OFF (сейчас **не течёт ни одно** событие). Ключи-мосты каталога (`ayla_service_id`, `ayla_user_id`) **никогда не заполняются** ни одним runtime-путём. Зеркало каталога кормится из legacy **mysite**, а не из Ayla.

**Первый кодовый фокус (подтверждён):** Stream 0 — Contract Stabilization (`AylaUrlBuilder` + унификация auth + фикс путей/токенов), затем event_id width, затем catalog-bridge population. Без этого любые фичи ломаются на стыке.

---

## 1. Repo ownership (по факту кода)

| Домен | Canonical owner | Роль bot-platform | Подтверждение |
|---|---|---|---|
| User / PII / Profile / SpecialistProfile | **Ayla** `users/models.py` (`AUTH_USER_MODEL='users.User'`) | зеркалит только `display_name`+`avatar_url` на `BotUser` | ✅ соответствует ADR-0009 |
| Booking / availability / расписание | **Ayla** `appointments/` | mirror `RemoteBookingProxy` + reminders/callbacks | ⚠️ по умолчанию bot владеет локально (флаг OFF) |
| Каталог / услуги / цены | **Ayla** `services/` | mirror `apps/catalog/*` | ❌ зеркало кормится из mysite, не Ayla |
| Платежи | **Ayla** `payments/` (+YooKassa) | наблюдает события, retry existing; НЕ создаёт | ✅ подтверждено |
| Reviews / analytics / nutrition | **Ayla** | consumers / клиенты | частично |
| Channel identity / conversations / skills / memory / KB | **bot-platform** | владелец | ✅ |
| Cross-service events (publish) | **Ayla** outbox | consumer (ingest) | ✅ контур есть, доставка OFF |
| AI orchestration | bot-platform `apps.llm.router` (собственный); `ayla-ai-core` — узко (voice/fallback + boot-лог) | — | ✅ |

**Важная поправка к брифингу:** у Ayla **есть** top-level `users/`-приложение (брифинг ошибочно утверждал обратное). Django-структура плоская, apps по DDD (`application/domain/infrastructure/internal_api.py`).

---

## 2. Master GAP MAP

Статусы: 🟢 готово · 🟡 частично/по-флагу · 🔴 сломано/риск · ⚪ отсутствует (ожидаемо).

| MVP-блок | Ayla (canonical) | bot-platform | Статус | Что делать |
|---|---|---|---|---|
| **Contract: URL/base_url** | host-only, `AylaUrlBuilder` есть | 5 ad-hoc f-строк, нет builder | 🔴 | Портировать `AylaUrlBuilder` в bot, прогнать все клиенты |
| **Contract: auth унификация** | `AYLA_INTERNAL_API_TOKEN` + `NUTRITION_SERVICE_TOKEN` | 2 секрета × 3 стиля; `AYLA_SERVICE_TOKEN` не существует у Ayla | 🔴 | Свести к `AYLA_INTERNAL_API_TOKEN`; фикс profile/recs |
| **Booking create** | `POST internal/appointments/` идемпотентный | client + adapter (флаг ON); **локальный `BookingRequest` (флаг OFF)** | 🟡 | Флаг OFF в пилоте → латентное ADR-нарушение |
| **Availability (slots/dates)** | `slots/` + AvailabilityQueryService | client fan-out (14 дней) | 🟡 | Фикс: `service_id` обязателен у Ayla, опционален в клиенте → 400 |
| **Cancel / reschedule** | native endpoints (reschedule хранит id) | client + mirror | 🟡 | Нет server-side idempotency на internal cancel/reschedule |
| **me/bookings** | cursor-paginated | `get_user_appointments` | 🟢 | — |
| **Каталог модели** | full (`ServiceTemplate`/`Service`/`RegionalPricing`) | mirror `CatalogService` | 🟢 (по отдельности) | — |
| **Catalog bridge (`ayla_service_id`)** | UUID есть | колонка есть, **никогда не пишется** | 🔴 | Заполнять при sync из Ayla / backfill |
| **Master bridge (`ayla_user_id`)** | `SpecialistProfile.id` | колонка есть (задвоена!), не пишется | 🔴 | Убрать дубль поля + заполнять |
| **Catalog sync source** | canonical endpoints (не используются) | тянет из **mysite** | 🔴 | Перенацелить sync на Ayla internal catalog |
| **Health-check grounding** | флаг на `ServiceTemplate`, **не на `Service`** | mirror из mysite | 🔴 | Определить canonical-дом флага на `Service` |
| **Pilot Discovery Ranking** | full 3-layer ranking (`catalog_recommendations_api`) | proxy (Mini-App only), chat-discovery без ранжирования (order by name) | 🟡 | 4-фазный план в §7 Stream 4: Фаза 1+2 в пилот (trust/geo/goal/price, без личной истории), Фаза 3 fast-follow, Фаза 4 (персонализация) post-pilot |
| **`review_count` в mirror** | есть в Ayla (`reviews_count`) | **отсутствует** в `CatalogMaster` | 🔴 | Тикет **#1060** (S4.0, linked #1044) — нужен для Bayesian trust-score (Фаза 1) |
| **Search** | `GlobalSearchView` (icontains+PG rank+haversine) | нет | 🟢 (Ayla-only) | — |
| **MAX parse/outbound/keyboards** | — | complete | 🟢 | — |
| **Booking skill (E2E)** | — | complete (masters→slots→confirm) | 🟢 | — |
| **Consent-гейт (глобальный бот)** | — | **не срабатывает** (глоб. путь мимо скиллов) | 🔴 | founder-P0: провести глоб. путь через consent+safety |
| **Safety pre/post-check** | — | в `pipeline.turn()` — **dead code в проде** | 🔴 | Вкрутить pre_check в оба MAX-хендлера |
| **Skill `should_handoff`** | — | **игнорируется** хендлером MAX | 🔴 | Обработать `should_handoff` → `create_admin_task` |
| **Handoff service** | — | create/resolve/transcript (PII-aware) | 🟢 | — |
| **Eventbus ingest (HMAC/dedupe/DLQ)** | — | complete, 13 consumers | 🟢 | — |
| **Event delivery (Ayla→bot)** | outbox + publisher + HMAC | ingest ready | 🟡 | double-gated OFF; сначала фикс event_id |
| **event_id формат** | UUID 36 симв. | колонки `varchar(26)` (ULID) | 🔴 | Расширить колонки до 36 (или Ayla→ULID) |
| **Payments ownership** | canonical + YooKassa | наблюдает, НЕ создаёт | 🟢 | подтверждено |
| **payment.failed DM (N=3)** | эмитит payment.failed | exactly-once DM через dedupe | 🟢 | — |
| **Notifications channel split** | mobile push (in-proc) | MAX DM + reminders | 🟡 | Риск двойного контакта; prefs-dispatcher не собран |
| **Identity bridging** | `resolve_external_user`→proxy+TUR | service Bearer + `X-External-User-ID`, без JWT | 🟢 | — |
| **AI memory (yellow/red)** | DOB endpoint отсутствует (#597) | fail-closed → 100% drop | 🟡 | По дизайну безопасно, но не функционально |
| **UserPersonalContext ownership (#1055)** | declared prefs (`users/models.py:412`, wired мобильный чат) | inferred memory (`identity/models.py:534`, G2/MEM) | 🟢 ACK latent (P2) | Граница declared(Ayla)/inferred(bot), Вариант B; конвергенция post-pilot MEM; pre-pilot только docstring |

---

## 3. Что готово (можно опираться, не трогать без нужды) — «Do not rebuild»

- **MAX-канал:** `parser.py`/`handler.py`/`outbound.py` — 3 update-типа, inline-клавиатуры, идемпотентность вебхука 24h TTL, photo-pipeline. Solid.
- **Booking-скилл E2E:** `apps/skills/booking/skill.py` — masters→date→slot callback-цепочка, anti-hallucination allow-sets, health-check gate, провайдер YClients-или-Ayla-REST.
- **Handoff-сервис:** `apps/handoff/services.py` — атомарный `create_admin_task` (task+state flip+event+audit), PII-aware `package_transcript` (sha256 телефона, последние 20 сообщений).
- **Eventbus ingest:** HMAC+timestamp (±300s, constant-time, fail-closed), dedupe INSERT-first, DLQ, handler registry, 13 consumers, cross-tenant spoof-guards.
- **Ayla appointments:** `Appointment` + state machine (`pending→awaiting_payment→confirmed→completed`, `→cancelled/no_show`), outbox с dual-delivery полями, `IdempotencyKey`, working-hours/time-off availability.
- **Ayla recommendations:** реальный 3-layer ranking (top-3, distance+rating+availability, reasoning text, без платного продвижения) — «Ayla на стороне пользователя» уже в коде.
- **Identity bridging:** подтверждено отсутствие хранения client JWT; PII-минимизация с обеих сторон (internal profile endpoint отдаёт 2 поля).
- **Payments ownership:** bot не создаёт canonical payments; `create_payment` — REST-wrapper за `CERTIFICATE_PAYMENT_ENABLED=False`.
- **admin_api:** services↔masters mapping (HMAC optimistic concurrency), master deactivation (cascade), availability approve/reject — реализованы.

---

## 4. Что частично (доводить до контракта)

- **Booking через Ayla REST** — code-complete, но `BOOKING_VIA_AYLA_REST=false`; пилот идёт на локальном `BookingRequest`+YClients. `RemoteBookingProxy` пишется только при флаге ON → смена источника grounding при флипе.
- **Marketplace-light** — два разрозненных полу-потока: (1) реальный top-3, но только Mini-App и без перехода к слотам; (2) chat-discovery доходит до booking, но список без ранжирования (order by name). Единого intent→top3→slots нет.
- **Event delivery** — контур полный в обе стороны, но double-gated OFF; 5 consumer'ов без publisher'а (review.created, service.updated, master.schedule.updated, user.profile.updated, payment.authorized) — инертны.
- **AI memory yellow/red** — fail-closed из-за отсутствия DOB-endpoint (#597); green-zone работает.
- **Notifications** — оба канала есть (Ayla mobile push in-proc + bot MAX), но дедупликация контакта — «по дисциплине контракта», `MasterNotificationPrefs` dispatcher не собран.

---

## 5. Что ОПАСНО (ранжированные риски)

### P0 — блокеры пилота / безопасность

1. **Глобальный пилотный бот без consent-гейта и safety-слоя.** `handle_global_max_event` (`handler.py:340`) минует реестр скиллов → `privacy_consent`/`health_screening`/`human_handoff` не срабатывают на **том самом** боте для июльского пилота. Совпадает с founder-P0 в памяти. Сообщение «я думаю о суициде» на per-tenant пути тоже проваливается (safety pre_check живёт только в неиспользуемом `pipeline.turn()`).

2. **Эскалации теряются молча.** `handle_max_event` не читает `SkillResult.should_handoff` (`handler.py:540-579` vs `pipeline.py:792`). Booking-фейл пишет «переключаю на менеджера», но AdminTask не создаётся и бот продолжает отвечать.

3. **`event_id` width mismatch (latent).** Ayla эмитит UUID 36 симв. (`envelope.py:230`), bot-колонки `varchar(26)` (`eventbus/models.py:195,236,333`). Первый dedupe-INSERT → Postgres DataError → 500 → Ayla ретраит до dead-letter. Замаскировано только выключенной доставкой. **Чинить до любого флипа топика.**

### P1 — must-fix pre-pilot (стыки контрактов)

4. **`profile_client` сломан дважды:** путь `/api/v1/users/{id}` вместо `/api/v1/internal/users/{user_id}/` + токен `AYLA_SERVICE_TOKEN` вместо `AYLA_INTERNAL_API_TOKEN` (`profile_client.py:155,157`). 100% fetch → вечный dead-letter `user.profile.updated`.

5. **`recommendations_client` сломан дважды:** путь без `/api/v1` (404) + не тот токен (403) (`recommendations_client.py:89,91`).

6. **s2s-auth фрагментация.** `AYLA_SERVICE_TOKEN` не существует у Ayla; `nutrition_client` шлёт `X-Service-Token: AYLA_SERVICE_TOKEN`, Ayla ждёт `NUTRITION_SERVICE_TOKEN` — работает только если деплой выставит равными. Только `booking_client` полностью совпадает.

7. **Мёртвые ключи-мосты каталога.** `ayla_service_id` и `CatalogMaster.ayla_user_id` **не пишутся** ни sync-ом, ни миграциями, ни consumer'ами (только `.filter()` по ним). Под `BOOKING_VIA_AYLA_REST` health-check grounding fail-closed на каждом miss → все gated-записи в manual handoff.

8. **Источник истины каталога = mysite, не Ayla.** `http_client.py` тянет `MYSITE_CATALOG_BASE_URL`; canonical endpoints Ayla существуют, но не используются → латентное ADR-0009 rule-1/2.

### P2 — латентные / архитектурные

9. **ADR latent violation booking.** Флаг OFF → локальный `BookingRequest` де-факто canonical в пилоте (согласуется с памятью про yclients-shrink deferred).
10. **`UserPersonalContext` name collision (#1055) — РЕШЕНО ACK latent (P2).** Не «плохой дубль», а два разных понятия под одним именем: declared prefs (Ayla, wired мобильный чат — не трогаем) vs inferred memory (bot, G2/MEM). End-state Вариант B, конвергенция post-pilot MEM. Не pilot-blocker.
11. **`Service.requires_health_check` живёт на `ServiceTemplate`, не на bookable `Service`** — даже с рабочим мостом флаг не прочитать из canonical.
12. **`booking.no_show` + `tenant.relationship.revoked`** эмитятся Ayla, но нет consumer'а → 422+DLQ если внести в allowlist.
13. **Нет server-side idempotency** на internal cancel/reschedule; reschedule не идемпотентен натурально.
14. **Задвоенное поле** `ayla_user_id` в `CatalogMaster` (`models.py:216` и `:234`).
15. **Дубль-контакт уведомлений** (один OutboxEvent → mobile push + MAX) при флипе топика.
16. **Retention cleanup** dedupe/DLQ не собран (unbounded рост).
17. **Два расходящихся MAX-хендлера** (per-tenant vs global) дублируют persistence/idempotency → дрейф.
18. **`review_count` отсутствует в mirror** (#1060) — блокирует Bayesian trust-score дискавери (Stream 4 Фаза 1); данных сейчас нет, нужно поле + populate на синке (linked #1044).
19. **DTO-утечка коммерческих полей** (Stream 4 Фаза 2) — расширение discovery-DTO = риск протащить price/commercial в cross-tenant выдачу; на каждое поле ручной `_to_card` + `test_dto` + MKT1-линт.
20. **Availability fan-out** (Stream 4 Фаза 3) — дёрганье Ayla-слотов на выдачу = N запросов; нужен кэш + лимит топ-N, иначе латентность/нагрузка.

---

## 6. Что НЕ трогаем (границы для агентов)

- **Не переписывать** booking engine, каталог, users, search, eventbus, handoff — всё есть. Только доводка контракта.
- **Не создавать** новые transactional-домены в bot-platform (ADR-0009 rule #4).
- **Не флипать** `BOOKING_VIA_AYLA_REST`, `OUTBOX_EXTERNAL_DELIVERY_TOPICS`, `external_delivery_enabled`, `CERTIFICATE_PAYMENT_ENABLED` без прохождения соответствующих gate'ов ниже.
- **Не менять** контракт `booking_client` (он эталон — под него равняем остальных).
- **Nutrition/wellness** — держать отдельным потоком, не мешать booking/marketplace (память: Wellness MVP scaled).
- **Certificate payments** — deferred post-pilot (память), не включать live.

---

## 7. Задачи для код-агентов (потоки → тикеты)

> **v1.1 — нумерация стримов пересмотрена** (по ревью founder 2026-07-02): Stream 0 разбит на A/B/C; `event_id` вынесен в отдельный **Stream 0.5** (до любых флипов доставки); **safety поставлен раньше booking**. Каждый пункт связан с заведённым GitHub-тикетом.

### Stream 0 — Contract Stabilization (ПЕРВЫМ)
Repo: ai-bot-platform. Allowed: `apps/integrations/ayla/**`, `apps/integrations/ayla_payments/**`, `config/settings/**`, `docs/architecture/contract-matrix.md`, `docs/architecture/api-spec-contract-drift-audit.md`, тесты. **Forbidden:** `apps/booking/**`, `apps/channels/**`, `apps/conversations/**`, `apps/eventbus/**`, `apps/catalog/**`.

**S0-A — URL / Auth contract** (#1049, #1050)
- Портировать `AylaUrlBuilder` (из Ayla `core/ayla_urls.py`) в `apps/integrations/ayla/`; host-only валидатор `AYLA_BASE_URL` (отвергает scheme-in-path/двойной префикс); нормализовать trailing slashes.
- Унифицировать s2s-секрет: свести Bearer на `AYLA_INTERNAL_API_TOKEN`; nutrition — переименовать в `NUTRITION_SERVICE_TOKEN` или задокументировать invariant равенства. ADR по s2s-auth.
- **DoD:** нет ручных `f"{AYLA_BASE_URL}/api..."` в `integrations/ayla`; все клиенты через builder; все используют `AYLA_INTERNAL_API_TOKEN` кроме явно исключённых; `contract-matrix.md` обновлён.

**S0-B — Client path fixes** (#978, #1048)
- `profile_client`: путь → `/api/v1/internal/users/{user_id}/`, токен → `AYLA_INTERNAL_API_TOKEN` (см. #978 + комментарий про токен).
- `recommendations_client`: путь → `/api/v1/internal/me/catalog/recommendations/`, токен → `AYLA_INTERNAL_API_TOKEN` (#1048).
- **DoD:** оба клиента резолвятся 200 против реального роута Ayla (или мок по route-table); circuit breaker у recs.

**S0-C — Contract tests** (acceptance S0-A/B)
- Тест, диффящий пути/токены клиентов против route-table/OpenAPI Ayla; в CI.
- **DoD:** тест падает при любом расхождении путь/метод/токен.

### Stream 0.5 — Event ID Compatibility (до любых флипов доставки)
Repo: ai-bot-platform (+ Ayla если ULID). Allowed: `apps/eventbus/**`, тесты. **Forbidden:** `apps/channels/**`, `apps/admin_api/**`.
- **P0** `event_id` 36 симв. или ULID-agreement (#1058) — расширить `IngestDedupe`/`IngestDLQ`/`HandlerFailureTracker` + валидация длины в `ingest_envelope`.
- `booking.no_show` + `tenant.relationship.revoked` — вне allowlist или с consumer'ом (#946 + комментарий).
- Retention cleanup beat (#1056); notification double-contact + `MasterNotificationPrefs` dispatcher (#1057).
- **DoD:** реальное Ayla-событие (UUID-36) проходит dedupe без DataError; эмитируемые без consumer'а топики не в allowlist; retention-beat чистит.

### Stream 1 — Global Safety / Consent (P0, до booking flip)
Allowed: `apps/channels/max/**`, `apps/orchestrator/**`, `apps/conversations/**`, `apps/handoff/**`, `apps/identity/**`, `apps/consent/**`, тесты.
- Consent-гейт на глобальном пути (#1046, Variant A soft gate; расширяет #1026/#956) — фронт-часть отдаётся ShiroPy.
- **P0** `SkillResult.should_handoff` → `create_admin_task` + заглушить бота (#1047).
- Судьба `pipeline.turn()`: вкрутить или портировать pre_check/should_handoff в оба хендлера, убрать дрейф (#1053).
- `ConsentRecord.has_consent` → `memory_writer` (#1054); DOB endpoint Ayla-side (beautygo #202).
- **DoD:** global MAX path вызывает consent-гейт и safety pre_check; `should_handoff` создаёт AdminTask; при HUMAN_HANDOFF бот молчит; тесты на red-flag / complaint / «оператор» / booking failure; старый per-tenant путь не затронут (регресс).

### Stream 2 — Booking via Ayla REST (после S0 + Stream 1)
Allowed: `apps/booking/**`, `apps/bookings/**`, `apps/integrations/ayla/booking_client.py`, тесты. **Forbidden:** `apps/catalog/**`, `apps/channels/max/**`, `apps/eventbus/**`.
- Достроить FOUNDATION-клиент (#1016); slots `service_id` fix (#1051); server-side idempotency cancel/reschedule (Ayla beautygo #203).
- Авто-провижининг `ayla_user_id`; provider walk-in (#1017); health-screening grounding (#1034); miniapp booking через REST (#996).
- Подготовить flip-план `BOOKING_VIA_AYLA_REST` (grounding source consistency).
- **DoD:** E2E-тест: bot читает слоты/каталог + create/cancel/reschedule через Ayla REST без YClients и без локальной canonical-записи; нет двойного бронирования с walk-in.

### Stream 3 — Catalog bridge (после S0)
Repo: оба. Allowed: `apps/catalog/**`, `apps/orchestrator/discovery.py`, тесты + Ayla `services/`. **Forbidden:** `apps/booking/services/create.py`, `apps/eventbus/**`.
- Чистый ребилд каталога (#1044, заменяет #1043) — 4-слойная модель + `YClientsMapping`; Ayla-модель beautygo #200.
- Убрать дубль поля `ayla_user_id` (#1052); заполнять stable-id/`ayla_service_id`; ретайр mysite-sync.
- Ayla-side: canonical-дом `requires_health_check` на `Service`; publisher'ы `service.updated`/`master.schedule.updated`.
- **DoD:** `ayla_service_id` coverage ≥ порога; sync с Ayla (не mysite); health-check grounding читает canonical.

### Stream 4 — Pilot Discovery Ranking (после Stream 2 + Stream 3)
> **Название намеренно НЕ «marketplace-light»:** в пилот входит только ранжирование дискавери (relevance+trust+geo+goal/price). Полноценный marketplace (фильтры, геолокация, персонализация, 3 слоя, история, Mini App) — post-pilot.

Allowed: `apps/marketplace/**`, `apps/orchestrator/discovery.py`, `apps/integrations/ayla/recommendations_client.py`, `apps/catalog/**` (только для `review_count`), тесты. **Forbidden:** `apps/booking/services/create.py`, `apps/eventbus/**`.

**Архитектурная развилка (принято):** полноценная персонализация на глобальном пути конфликтует с consent-гейтом (#1046) и требует памяти G2 → **post-pilot**. «Любимый мастер» сейчас считается в `ClientProfile` (per-tenant), на глобальном боте тенанта нет; кросс-тенантная история = часть G2. Для пилота: «умно, но **без личной истории**» = relevance + trust + geo + availability. Это честная граница, её принимаем.

**Данные-топливо (есть):** `CatalogMaster` (rating, specialization, bio, experience, is_active, tenant→city), `CatalogService` (**goals** JSON, is_popular, price_from, duration_min, contraindications, requires_health_check), `MasterServiceOffering` (master↔service → goal/price-aware джойн), Ayla-клиенты (booking/recommendations/profile). `ClientProfile` (preferred_master_id, rfm) — **per-tenant** (см. развилку).

**Фаза 1 — быстрые улучшения (В ПИЛОТ, ~2–3 дня, без внешних зависимостей):**
- синоним-recall перед `icontains` («бровист»→«брови»);
- **Bayesian trust-score** `(rating·n + C·m)/(n+C)` — рейтинг 5.0 из 1 отзыва не обгоняет 4.8 из 200. **Требует `review_count` в mirror — сейчас его НЕТ** → отдельный тикет **#1060** (S4.0, linked #1044, pilot must-have; поле в mirror + populate на синке);
- trust-floor (Guardian-lite) — прятать мастеров ниже порога при достаточном n;
- diversity — не более 2 мастеров с одного салона в топе;
- reasoning-текст (шаблон): «★ 4.9 · 120 отзывов · Пенза»;
- fallback пустого результата (соседние города / снять фильтр).

**Фаза 2 — скоринговый движок + goal/price (В ПИЛОТ, ~1–1.5 нед, джойн каталога):**
- единая функция скоринга (взвеш. сумма, норм. 0..1): relevance (макс. вес) · trust · geo · popularity · diversity_penalty;
- goal/price-aware через `MasterServiceOffering → CatalogService.goals/price_from` («убрать отёчность до 2000₽»);
- расширить LLM-tool `show_masters` параметрами goal/price_max/sort;
- ⚠️ **риск:** DTO-расширение — единственное место лёгкой утечки коммерческих полей в cross-tenant выдачу → на КАЖДОЕ новое поле ручной `_to_card` + пин `test_dto` + прогон MKT1-линта.

**Фаза 3 — availability-aware (fast-follow/post-pilot, ~1 нед, зависит от G8 `booking_client.get_slots`):**
- буст за ближайшую доступность — дёргать Ayla-слоты **только для топ-N** кандидатов, с **кэшем слотов** (иначе N запросов на выдачу → латентность/нагрузка на Ayla);
- availability в reasoning: «есть окно завтра в 15:00».

**Фаза 4 — персонализация + 3 слоя (POST-PILOT, зависит от G2 + согласия):**
- 3-слойная выдача (Tau): «Твои» / «Ayla подобрала» / «Исследовать»;
- персональный буст (любимый мастер/категория из памяти) — **только при согласии** (Вариант A гейтит чтение памяти);
- кросс-тенантная история (агрегат «любимых» по всем салонам — часть G2);
- ИИ-reasoning вместо шаблонов.

**MAX-ограничения (в дизайне):** нет carousel/card → выдача = текст + кнопка на мастера, ≤29 рядов; нет geolocation API → гео только по городу (текст/кнопки-города); 3 слоя = 3 текстовых блока с разделителями.

**В пилот:** Фаза 1 целиком + Фаза 2 (без availability). **Fast-follow:** Фаза 3. **Post-pilot:** Фаза 4.
**DoD (пилот):** «хочу массаж завтра» → релевантный ранжированный список (trust+geo+goal/price, без личной истории) → слоты → booking в Ayla — один связный путь; коммерческие поля не текут в cross-tenant DTO (MKT1 green).

### Arch ACK — #1055 UserPersonalContext (РЕШЕНО 2026-07-02, ACK latent, НЕ pilot-blocker)
Два разных понятия под одинаковым именем класса (не «плохой дубль», а плохое именование двух сущностей):
- **Ayla `users.UserPersonalContext` = declared preferences** (юзер сам выбрал/подтвердил в UI/мобильном чате: районы, время, диета, timezone/city). Ближе к профилю → **canonical в Ayla**. Wired + tested + обслуживает мобильный in-app AI-чат (`ai/personal_context_hint.py`, DRF-174/DRF-230) — **не трогаем и не ретайрим до пилота**.
- **bot `UserPersonalContext`/`MemoryEntry` = inferred conversational memory** (что AI вывел из диалога, cross-channel, zones, minor_lock). → **bot-platform / ayla-ai-core**. Это gap **G2 / MEM**, fast-follow post-pilot; в ①Technical Pilot MAX-бот от unified memory НЕ зависит.
- **End-state = Вариант B** (по умолчанию): declared остаётся в Ayla; inferred — в bot; мобильный чат читает inferred через proxy/service boundary на MEM. Вариант A («всё в bot») **не берём** — большой cross-repo трек + риск для живого мобильного чата.
- **Разрешено pre-pilot (дёшево/безопасно):** ACK в #1055; cross-ref docstring в bot-модели; заметка в этом документе. **Запрещено pre-pilot:** удалять/переименовывать Ayla-модель, переносить declared prefs, unified memory service, менять mobile onboarding write-path, делать bot-memory обязательной зависимостью пилота.
- Приоритет: **P2 / latent**. Дальнейшее — `MEM-1` (граница declared/inferred), `MEM-2` (end-state A/B), `MEM-3` (rename/migrate) — всё **post-pilot MEM**.

---

## 8. Первый pilot milestone (нить пилота)

**Цель:** пользователь в MAX записывается через глобального бота, безопасно и с согласием, запись создаётся в Ayla. Каждый PR оцениваем по вопросу: **приближает ли он эту нить?**

1. Инбаунд MAX (глоб.) → **consent-гейт** → **safety pre_check** → discovery.
2. Discovery → **ранжированный** top-3 → handoff в tenant booking.
3. Слоты (date→time) → confirm → **booking в Ayla REST** (флаг ON) → `RemoteBookingProxy`.
4. Ayla эмитит `booking.created/confirmed` → **доставка включена по топику** → bot обновляет mirror + ставит reminder.
5. Любой фейл/red-flag → **AdminTask** (не молчаливый дроп).

**Критический путь (parallelised):** `S0 (контракты) → S0.5 (event_id) → Stream 1 (consent+safety+handoff) → Stream 3 (catalog bridge для health-check) → Stream 2 (booking flip) → пер-топик flip доставки → Stream 4 (marketplace E2E)`.

> **Решение founder 2026-07-02:** пилот на **Ayla REST = 15.08.2026** → FOUNDATION #1016 + P0 booking + event-delivery остаются pilot-critical. **15.07 НЕ фиксируется как Ayla-REST MVP** (P0/P1-блокеры) — если нужен показ 15.07, только демо/legacy YClients без заявления «MVP готов». Тело EPIC #1044 («пилот на легаси») надо переформулировать под это. План/сроки — в [`2026-07-02-MVP_DELIVERY_TRACKER.md`](2026-07-02-MVP_DELIVERY_TRACKER.md).

---

## 9. Release gates (что должно быть выполнено ДО флипов)

| Gate | Условие | Блокирует |
|---|---|---|
| **G-Contract** | S0-A/B/C: все клиенты через `AylaUrlBuilder`, единый токен, контракт-тест зелёный | любой прод-трафик bot→Ayla |
| **G-Event** | `event_id` width согласован (#1058); `booking.no_show`/`tenant.relationship.revoked` вне allowlist или с consumer'ом | flip `OUTBOX_EXTERNAL_DELIVERY_TOPICS` |
| **G-Safety** | глоб. путь через consent+safety; `should_handoff` создаёт AdminTask | запуск пилотного бота |
| **G-Booking** | slots `service_id` fixed; cancel/reschedule idempotency; `ayla_user_id` провижининг | flip `BOOKING_VIA_AYLA_REST` |
| **G-Catalog** | `ayla_service_id` coverage ≥ порога; sync с Ayla; health-check дом определён | health-check grounded booking под флагом ON |
| **G-Notify** | де-конфликт двойного контакта per-event | flip топиков payment/booking доставки |

---

## 10. Freeze rule

До закрытия **G-Contract, G-Event и G-Safety** запрещены:
- новые продуктовые фичи;
- новые transactional-модели в bot-platform (ADR-0009 rule #4);
- новые marketplace-фильтры;
- новые wellness-сценарии;
- флип booking / events / payment флагов;
- изменения в `booking_client` без отдельного approval.

**Правило new-scope:** любое увеличение pilot-scope должно СНАЧАЛА попасть в Delivery Tracker как **New Scope SP** (velocity baseline adjustment) — иначе задача не берётся в работу. Без этого delta через неделю будет врать (пример: дискавери 13→29 SP).

**Жёсткое правило для агентов:** любой агент, создающий новый booking/catalog/user/payment transactional-домен в bot-platform, **делает неверную задачу** — блоки уже есть, задача = связать, не дублировать.

---

## 11. PR sequencing — первые PR

> **v1.2:** S0-A разгружен (только builder+auth+booking_client-эталон); S0-B — единственное место, где трогаются profile/recommendations/nutrition/user_proxy/payments (перевод на builder + фиксы). S0-C после A/B. S1 — epic из 3–4 PR.

| # | Задача | Тикет(ы) | Repo | Branch | Forbidden dirs |
|---|---|---|---|---|---|
| 1 | AylaUrlBuilder + `AYLA_BASE_URL` validator + auth-примитивы + booking_client как эталон | #1049, #1050 | ai-bot-platform | `fix/s0a-ayla-url-auth` | booking, channels, eventbus, catalog; **не трогать др. клиенты (это S0-B)** |
| 2 | event_id 36 / ULID + validation + allowlist | #1058, #946 | ai-bot-platform (/Ayla) | `fix/s05-event-id-width` | channels, admin_api |
| 3 | перевод profile/recommendations/nutrition/user_proxy/payments на builder + path/token фиксы | #978, #1048, #1050 | ai-bot-platform | `fix/s0b-client-migrate` | booking, channels, eventbus (**после PR #1**) |
| 4 | S1-A global onboarding/consent gate | #1046 | ai-bot-platform | `feat/s1-global-safety-consent` (epic) | catalog, eventbus, booking/services/create |
| 5 | S1-B safety pre_check (оба хендлера) · S1-C should_handoff→AdminTask+mute · S1-D регресс/дрейф | #1053, #1047 | ai-bot-platform | (тот же epic, отдельные PR) | как выше |
| 6 | contract tests vs Ayla route-table | (S0-C) | ai-bot-platform (+Ayla) | `test/s0c-contract-tests` | — (**после PR #1+#3**) |
| 7 | catalog rebuild + `review_count` | #1044, #1052, **#1060**, beautygo #200 | оба | `feat/s3-catalog-rebuild` | booking/services/create |
| 8 | booking REST flip plan | #1016, #1051, beautygo #203 | оба | `feat/s2-booking-ayla-rest` | catalog, channels/max, eventbus |

---

## 12. Волновой запуск агентов (база = 2 параллельных; 3-й точечно)

> **v1.2:** Волна 1 разбита на под-волны по зависимостям (S0-B зависит от S0-A; S0-C — от S0-A/B; S1 не смешиваем с интеграционными правками).

**Волна 1A (W1):** Agent **S0-A** (builder+auth, #1049/#1050 → PR #1) ‖ Agent **S0.5** (event_id, #1058 → PR #2).
**Волна 1B (конец W1 / W2):** Agent **S0-B** (перевод клиентов, #978/#1048 → PR #3, после S0-A) ‖ Agent **S1** (epic safety/consent/handoff, #1046/#1047/#1053 → PR #4–5) ‖ **ShiroPy** Consent/Welcome UI (#1046 фронт + #948/#949, ревьюим).
**Волна 1C (W2):** Agent **S0-C** (contract tests → PR #6, после S0-A/B).

**Волна 2 — booking + catalog bridge (W3–W4):**
- Agent **S3** — Catalog rebuild + `review_count` (#1044, #1052, #1060, beautygo #200).
- Agent **S2** — Booking via Ayla REST (#1016, #1051, beautygo #203).

**Волна 3 — Pilot Discovery Ranking (W4–W5, после S2+S3):**
- Agent **S4** — Фаза 1+2: trust/geo/goal/price ranking → slots → booking (#1018, #1020, #1060). Фаза 3/4 — fast-follow/post-pilot.

---

## Приложение A — расхождения с брифингом orch.txt (по факту кода)

- **Ayla `users`-app СУЩЕСТВУЕТ** (брифинг: «нет отдельной users»). Identity/PII там.
- **`booking_client` уже приземлён и корректен** (`contract-matrix.md` устарел: помечает booking как «MISSING no client»).
- **Marketplace top-3 существует** (в Ayla, реальный ranking) — но не связан с chat-путём.
- **`AYLA_SERVICE_TOKEN` не существует у Ayla** — источник фрагментации auth.
- **event delivery полностью выключен** — cross-service событий сейчас не течёт вообще.
- **catalog bridge-ключи мёртвые** — не Repomix-риск «проверить», а подтверждённый факт: не заполняются.

---

## Приложение C — карта заведённых тикетов (2026-07-02)

Новые issue по untracked-находкам аудита:

| Тикет | Стрим | Тема |
|---|---|---|
| ai-bot-platform #1058 | S0.5 | **P0** event_id width varchar(26) vs UUID-36 |
| ai-bot-platform #1047 | S1 | **P0** MAX handler теряет should_handoff |
| ai-bot-platform #1049 | S0-A | AylaUrlBuilder + AYLA_BASE_URL host-only |
| ai-bot-platform #1050 | S0-A | Унификация s2s-auth (AYLA_SERVICE_TOKEN не существует) |
| ai-bot-platform #1048 | S0-B | recommendations_client путь+токен |
| ai-bot-platform #1051 | S2 | slots service_id 400 fan-out |
| ai-bot-platform #1052 | S3 | дубль поля ayla_user_id в CatalogMaster |
| ai-bot-platform #1053 | S1 | pipeline.turn() dead code (safety) |
| ai-bot-platform #1054 | S1 | ConsentRecord → memory_writer |
| ai-bot-platform #1055 | ACK | дубль UserPersonalContext (ownership) |
| ai-bot-platform #1056 | S0.5 | retention cleanup beat |
| ai-bot-platform #1057 | S0.5 | double-contact + MasterNotificationPrefs dispatcher |
| beautygo_backend #203 | S2 | idempotency internal cancel/reschedule |
| beautygo_backend #202 | S1 | DOB/is_adult endpoint (yellow/red memory) |

Расширены комментарием: **#978** (добавлен scope токена к профилю), **#946** (добавлен `tenant.relationship.revoked` без consumer'а).

Уже существовавшие ключевые: EPIC **#1014** (nationwide booking), **#1016** (FOUNDATION REST client), **#1046** (consent-гейт P0), EPIC **#1044** (catalog rebuild, beautygo #200), **#1018/#1019/#1020** (marketplace/discovery), **#1034** (health grounding), **#996** (miniapp via REST).

---

## Приложение B — источники (агенты-разведчики)

Документ собран из 6 read-only аудитов: Booking+Availability, Catalog/Masters/Marketplace, Identity/PII/Memory, Contract Stabilization, MAX/Conversations/Handoff/Skills, Eventbus/Notifications/Payments/Reviews/Analytics. Перекрёстно подтверждённые находки (напр. `profile_client` mismatch — Identity+Contract; `recommendations_client` — Contract+Catalog) помечены как высокодостоверные.
