# MVP_AGENT_QUEUE_2026-07

> Очередь готовых заданий для код-агентов. Запуск **волнами; база = 2 параллельных агента** (velocity ~35 SP/нед). 3-й агент — **точечно на независимые задачи** (docs / tests / eventbus / catalog audit), не в базовой скорости, из-за пересечений bot ↔ Ayla. Пилот на Ayla REST = **15.08.2026**. Каждое задание: prompt · repo · branch · allowed/forbidden dirs · DoD · tests · risk · SP · issue.
>
> **Общие правила для всех агентов (вставлять в каждый prompt):**
> - Read-first: сначала прочитать релевантный код, потом менять. Не переписывать существующие блоки — задача = связать/починить, не дублировать.
> - **Freeze rule:** запрещены новые transactional-домены, новые фичи, флипы флагов (GAP MAP §10). Агент, создающий новый booking/catalog/user/payment домен, делает неверную задачу.
> - Соблюдать `allowed`/`forbidden` строго. PR в `dev`, Conventional Commits, ветка по имени ниже. Code Reviewer (friendly+adversarial) обязателен.
> - Не `git add .` — только именованные файлы (в репо параллельные WIP).

---

## ВОЛНА 1 — стабилизация стыков и безопасности (под-волны 1A/1B/1C · база 2 агента)

> Зависимости: **S0-B зависит от S0-A** (builder); **S0-C — от S0-A+S0-B**; S1 не смешивать с интеграционными правками. Поэтому НЕ запускать S0-A и S0-B параллельно.

### 1A · Agent S0-A — AylaUrlBuilder + auth-примитивы (PR #1) · ~8 SP · #1049 #1050
- **Repo:** ai-bot-platform · **Branch:** `fix/s0a-ayla-url-auth`
- **Allowed:** новый `apps/integrations/ayla/url_builder.py` + `__init__.py`, `apps/integrations/ayla/booking_client.py` (**как эталон**), `config/settings/**`, `docs/architecture/contract-matrix.md`, тесты builder'а.
- **Forbidden:** `profile_client.py`/`recommendations_client.py`/`nutrition_client.py`/`user_proxy.py`/`ayla_payments/**` (**это S0-B — не трогать**); `apps/booking/**`, `apps/channels/**`, `apps/eventbus/**`, `apps/catalog/**`.
- **Prompt:** «Портируй `AylaUrlBuilder` из Ayla `core/ayla_urls.py` в `apps/integrations/ayla/`. Host-only `AYLA_BASE_URL`; builder вставляет `/api/v1`, отвергает scheme-in-path и двойной префикс, нормализует trailing slash. Проведи через builder ТОЛЬКО `booking_client` (эталон-пример). Настрой auth-примитивы/settings: единый Bearer `AYLA_INTERNAL_API_TOKEN`; удали ссылки на несуществующий `AYLA_SERVICE_TOKEN`; для nutrition зафиксируй `X-Service-Token`=`NUTRITION_SERVICE_TOKEN` (переменная/invariant). **НЕ трогай остальные клиенты — их переводит S0-B.** Обнови `contract-matrix.md`.»
- **DoD:** builder + тесты (host-only, double-prefix reject, trailing slash); `booking_client` через builder; auth-settings готовы к использованию S0-B; `contract-matrix.md` обновлён.
- **Risk:** LOW-MED (узкий scope, booking_client уже эталон).

### 1A · Agent S0.5 — event_id compatibility (PR #2) · 13 SP · #1058 #946
- **Repo:** ai-bot-platform (+Ayla если ULID) · **Branch:** `fix/s05-event-id-width`
- **Allowed:** `apps/eventbus/**`, тесты. **Forbidden:** `apps/channels/**`, `apps/admin_api/**`, `apps/integrations/**`.
- **Prompt:** «Расширь `event_id` в `IngestDedupe`/`IngestDLQ`/`HandlerFailureTracker` до `max_length=36` (миграция) ИЛИ ULID-agreement — предпочтительно расширение (Ayla эмитит `uuid4()` 36). Валидация длины в `ingest_envelope` (fail-fast → DLQ вместо 500). Allowlist: `booking.no_show` + `tenant.relationship.revoked` эмитятся без consumer'а — держать вне external-delivery allowlist или добавить consumer (#946).»
- **DoD:** Ayla-событие UUID-36 проходит dedupe без DataError; регресс на длину; эмитируемые-без-consumer'а не в allowlist.
- **Risk:** MED (миграция; доставка OFF → безопасно сейчас).

### 1B · Agent S0-B — перевод клиентов на builder + path/token фиксы (PR #3, ПОСЛЕ S0-A) · ~12 SP · #978 #1048 #1050
- **Repo:** ai-bot-platform · **Branch:** `fix/s0b-client-migrate`
- **Allowed:** `profile_client.py`, `recommendations_client.py`, `nutrition_client.py`, `user_proxy.py`, `ayla_payments/client.py`, тесты.
- **Forbidden:** `url_builder.py` (готов в S0-A — только использовать); `booking_client.py`; booking, channels, eventbus, catalog.
- **Prompt:** «Переведи profile/recommendations/nutrition/user_proxy/payments на `AylaUrlBuilder` (из S0-A). Фиксы: `profile_client` путь `/api/v1/internal/users/{user_id}/` + токен `AYLA_INTERNAL_API_TOKEN` (#978, путь+токен ВМЕСТЕ); `recommendations_client` путь `/api/v1/internal/me/catalog/recommendations/` + токен `AYLA_INTERNAL_API_TOKEN` + circuit breaker (#1048); nutrition секрет alignment (#1050). Убери все ручные f-строки.»
- **DoD:** нет `f"{AYLA_BASE_URL}/api..."` во всех клиентах; profile/recs резолвятся 200 против route-table; recs с circuit breaker.
- **Risk:** LOW (после S0-A). Единственное место, где трогаются эти 5 клиентов.

### 1B · Agent S1 — global safety/consent/handoff (EPIC, PR #4–5) · 26 SP · #1046 #1047 #1053
- **Repo:** ai-bot-platform · **Branch:** `feat/s1-global-safety-consent` (**epic, 3–4 PR — не один большой**)
- **Дробление PR:** **S1-A** global onboarding/consent gate (#1046, `global_onboarding.py` + ветка в `handle_global_max_event`); **S1-B** safety pre_check в оба хендлера (#1053); **S1-C** `should_handoff`→`create_admin_task`+HUMAN_HANDOFF mute (#1047); **S1-D** регресс-тесты + убрать дрейф двух хендлеров.
- **Allowed:** `apps/channels/max/**`, `apps/orchestrator/**`, `apps/conversations/**`, `apps/handoff/**`, `apps/consent/**`, тесты. **Forbidden:** `apps/catalog/**`, `apps/eventbus/**`, `apps/booking/services/create.py`, `apps/integrations/**`.
- **DoD:** global path через consent-гейт + safety pre_check; should_handoff → AdminTask + бот молчит при HUMAN_HANDOFF; `current_tenant() is None` в онбординге; регресс per-tenant пути.
- **Tests:** suicide/red-flag → handoff; complaint; «оператор»; booking failure → AdminTask; consent truth-table; повторный consent идемпотентен.
- **Risk:** HIGH (P0 safety). **FE (ShiroPy) параллельно:** consent/welcome UI — ревьюим.

### 1C · Agent S0-C — contract tests (PR #6, ПОСЛЕ S0-A+S0-B) · 11 SP
- **Repo:** ai-bot-platform (+Ayla route dump) · **Branch:** `test/s0c-contract-tests`
- **Allowed:** `apps/integrations/ayla/tests/**`, `docs/architecture/**`, CI-конфиг.
- **Prompt:** «Контракт-тест, диффящий пути+методы+auth всех Ayla-клиентов против route-table/OpenAPI Ayla. Падает при любом расхождении. В CI.»
- **DoD:** тест в CI зелёный и ловит искусственное расхождение.
- **Risk:** LOW.

---

## ВОЛНА 2 — booking + catalog domain rebuild (после Волны 1; S3 gated на 4 условия)

### Agent S2 — Booking via Ayla REST · 24 SP · #1016 #1051 · Ayla #203
- **Branch:** `feat/s2-booking-ayla-rest` · **Allowed:** `apps/booking/**`, `apps/bookings/**`, `apps/integrations/ayla/booking_client.py`, тесты (+Ayla `appointments/internal_api.py` для #203). **Forbidden:** `apps/catalog/**`, `apps/channels/max/**`, `apps/eventbus/**`.
- **Кратко:** slots service_id fix (#1051); server-side idempotency cancel/reschedule (Ayla #203); auto-provision `ayla_user_id`; RemoteBookingProxy consistency; flip-план; E2E confirm→Ayla REST→proxy.
- **DoD:** E2E-тест booking через Ayla REST без YClients/локальной canonical-записи; нет двойного бронирования с walk-in.

### Stream 3 — Catalog domain rebuild for Ayla booking (рефрейм #1044) · 50–70 SP
> **🚫 НЕ СТАРТОВАТЬ, пока не закрыты 4 условия:** (1) записано решение **G-CalendarSync** (Variant A Ayla-primary / Variant B YClients webhook→busy) по пилотному салону; (2) подтверждён источник данных Пензы; (3) принят Ayla-side breakdown; (4) S3 design locked. Модель: `ServiceTemplate`(таксономия) → `SalonService` → `SpecialistService`; `DraftSalonService`/`ExternalSourceMapping`. #1043/mysite удаляются. Онбординг = «Confirm, don't create».

**Agent S3A — Ayla catalog domain rebuild** · beautygo_backend · **20–30 SP** · #200
- **Отдельный Ayla-агент** (worktree на beautygo_backend). Расширить `ServiceTemplate` (goals/synonyms/icon/stable_slug); ввести `SalonService`; `SpecialistService`/связка `Service`; select-only enforcement (новые типы через draft/governance); stable-id internal API (отдаёт **resolved** duration + health-check + review_count); миграции+тесты.

**Agent S3B — bot catalog mirror from Ayla** · ai-bot-platform · **12–18 SP** · #1044 #1052 #1060
- **Branch:** `feat/s3b-bot-mirror-ayla` · **Allowed:** `apps/catalog/**`, тесты. **Forbidden:** `apps/booking/services/create.py`, `apps/eventbus/**`.
- `CatalogService` key = Ayla stable-id; sync из Ayla internal API; **удалить mysite-sync + весь #1043** (seed_from_mysite, C3, external_id-key, matcher/coverage/gate); убрать дубль `ayla_user_id` (#1052); `review_count` (#1060); resolved duration/health; KB/event consumers.

**Agent S3C — pilot salon intake + draft confirmation (minimal)** · beautygo_backend/admin · **8–13 SP**
- Собрать данные пилотного салона из 1–2 источников → `DraftSalonService` (pending/needs_review/confirmed) → normalize к ServiceTemplate → confirm → bookable `SalonService`/`SpecialistService`. Полная мульти-источниковая автоматизация — **post-MVP**.

**Agent S3-CAL — G-CalendarSync** · beautygo_backend + integration · **8–15 SP**
- Variant A (Ayla primary) ИЛИ Variant B (YClients inbound webhook → Ayla external busy intervals; company_id→tenant, staff_id→specialist). MVP-min = inbound-only (bot не предлагает занятые окна). **Блокирует G-Booking.**

**Agent S3D — catalog contract/API tests** · оба · **8–13 SP**
- stable-id; resolved duration/health; inactive/unconfirmed не в booking; mirror только из Ayla; mysite отсутствует.

**DoD Stream 3:** bot бронирует confirmed SpecialistService пилотного салона через Ayla REST; slots учитывают внешнюю занятость (G-CalendarSync); mysite удалён; contract-тесты зелёные.

---

## ВОЛНА 3 — Pilot Discovery Ranking (после S2 + S3)

### Agent S4 — Pilot Discovery Ranking (фазовый) · pilot ≈26 SP · #1018 #1020
- **Branch:** `feat/s4-discovery-ranking` · **Allowed:** `apps/marketplace/**`, `apps/orchestrator/discovery.py`, `apps/integrations/ayla/recommendations_client.py`, тесты. **Forbidden:** `apps/catalog/**` (`review_count` делает S3/#1060), `apps/booking/services/create.py`, `apps/eventbus/**`.
- **Prereq:** S4.0 `review_count` (#1060) уже сделан в S3 (Волна 2). Без него trust-score не считается.
- **Развилка (в prompt):** персонализация/личная история = post-pilot (конфликт с consent #1046 + зависит от G2). Пилот = relevance+trust+geo+goal/price, БЕЗ личной истории. **Название НЕ «marketplace» — не лезть в фильтры/гео-координаты/3 слоя/персонализацию.**
- **Фаза 1 (в пилот):** синоним-recall → Bayesian trust-score (использует `review_count` из #1060) → trust-floor → diversity ≤2/салон → reasoning-шаблон → fallback пустого результата.
- **Фаза 2 (в пилот):** единая функция скоринга; goal/price через `MasterServiceOffering→CatalogService.goals/price_from`; `show_masters` (goal/price_max/sort); нить recommendation→slots→booking. **⚠️ КАЖДОЕ новое DTO-поле = ручной `_to_card` + пин `test_dto` + MKT1-линт (не протащить коммерческое поле cross-tenant).**
- **Фаза 3 (fast-follow):** availability-буст только для топ-N + кэш слотов; availability в reasoning.
- **Фаза 4 (post-pilot):** 3 слоя (Твои/Ayla/Исследовать); персональный буст (память+согласие); кросс-тенантная история (G2); ИИ-reasoning.
- **MAX-огр.:** нет carousel → текст+кнопки ≤29 рядов; нет geolocation → гео по городу-строке.
- **DoD (пилот):** «хочу массаж завтра» → ранжировано (trust+geo+goal/price, без личной истории) → слоты → booking в Ayla; MKT1 green (нет утечки коммерческих полей).

---

> **Option B (2026-07-09): память для пилота В BOT.** Ayla M-A1/A2/A4 закрыты (bot apps/identity уже имеет зоны/Fernet/red-log/minor per ADR-0011/0006). Ayla держит declared-prefs+A1a. BOT M-B = ACTIVATE проводка concierge на существующие memory_reader/writer (НЕ клиент к Ayla-памяти). Ayla=central memory-сервис = post-pilot.

## STREAM 5 — Memory Foundation (⚠️ pilot-critical, ров · gated на §8 дизайн-дока)
> **✅ РАЗБЛОКИРОВАНО (§8 закрыт 2026-07-04): Fernet · green под PERSONAL_DATA (текст→#947) · fill observe-only · один ayla_user_id (телефон=merge-ключ).** 2-й Ayla-агент: agent-2=память M-A (#1094-1097). Тикеты M-B #1098-1100 / M-C #1101. ~~НЕ СТАРТОВАТЬ, пока не закрыт §8~~ `2026-07-03-MEMORY_FOUNDATION_DESIGN.md`: EncryptedField-подход · green consent implicit vs light-opt-in · fill-rate метрика+порог · global-identity (один ayla_user_id vs per-tenant merge). **Ownership:** Ayla владеет ВСЕЙ памятью (зоны 🟢🟡🔴 + шифрование); bot = read/write API-клиент по ayla_user_id (152-ФЗ: одно место хранения/удаления). BUILD фундамент → ACTIVATE узко (green+surfacing) → PLUG-IN post-pilot.

**Agent M-A — Ayla memory domain** · beautygo_backend · ~16 SP · #187
- **Отдельный Ayla-агент** (⚠️ конкурирует с catalog S3A за Ayla-capacity). zone-тэги+EncryptedField(yellow/red)+миграция (M-A1); skip/delete/wipe + RedZoneAccessLog 152-ФЗ (M-A2); internal read/write API по ayla_user_id, сервис-токен (M-A3, #187); behavioral-beat + метрики (M-A4).

**Agent M-B — bot concierge memory** · ai-bot-platform · ~13 SP · #1055 #1046
- **Branch:** `feat/m-b-concierge-memory` · **Allowed:** `apps/identity/**`, `apps/persona/**`/concierge, `apps/consent/**`, `apps/integrations/ayla/**` (новый memory-клиент), тесты. **Forbidden:** catalog, eventbus, booking/services/create.
- sentinel-tenant ayla_user_id resolve + Ayla personal-context клиент (M-B1, после M-A3); concierge read→инъекция + should_ask→write (M-B2); memory_* consent-типы + гейт green (M-B3).

**Agent M-C — ai-core surfacing** · ayla-ai-core · 3 SP
- context_builder принимает personal-context → surfacing в системный промпт concierge (M-C1). M-C2 contextual WRITE — post-pilot.

**DoD Stream 5:** зоны+шифрование в проде; бот органично спрашивает 1 green-поле и применяет; surfacing («помню…»); 152-ФЗ skip/delete/wipe + red не в GET; контекст на ayla_user_id переживает сессию; fill-rate считается.

---

## Deferred / буфер (W6+ или post-pilot)
S05.4 retention beat (#1056) · S05.5 double-contact dispatcher (#1057) · S1.6 de-drift handlers (#1053) · S1.7 ConsentRecord→memory (#1054) · S1.8 DOB endpoint (Ayla #202) · G.6 G-Notify · S4 Фаза 3 (availability) · S4 Фаза 4 (персонализация, 3 слоя) — зависит от G2.
**Arch ACK #1055 — РЕШЕНО (ACK latent, Вариант B):** declared prefs = Ayla `users.UserPersonalContext` (не трогать), inferred memory = bot. Pre-pilot разрешён только cross-ref docstring в bot-модели (ACK.1). Rename/migrate/unified — post-pilot MEM (MEM-1/2/3).
