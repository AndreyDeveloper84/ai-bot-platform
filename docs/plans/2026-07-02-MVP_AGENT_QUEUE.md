# MVP_AGENT_QUEUE_2026-07

> Очередь готовых заданий для код-агентов. Запуск **волнами; база = 2 параллельных агента** (velocity ~35 SP/нед). 3-й агент — **точечно на независимые задачи** (docs / tests / eventbus / catalog audit), не в базовой скорости, из-за пересечений bot ↔ Ayla. Пилот на Ayla REST = **15.08.2026**. Каждое задание: prompt · repo · branch · allowed/forbidden dirs · DoD · tests · risk · SP · issue.
>
> **Общие правила для всех агентов (вставлять в каждый prompt):**
> - Read-first: сначала прочитать релевантный код, потом менять. Не переписывать существующие блоки — задача = связать/починить, не дублировать.
> - **Freeze rule:** запрещены новые transactional-домены, новые фичи, флипы флагов (GAP MAP §10). Агент, создающий новый booking/catalog/user/payment домен, делает неверную задачу.
> - Соблюдать `allowed`/`forbidden` строго. PR в `dev`, Conventional Commits, ветка по имени ниже. Code Reviewer (friendly+adversarial) обязателен.
> - Не `git add .` — только именованные файлы (в репо параллельные WIP).

---

## ВОЛНА 1 — стабилизация стыков и безопасности

### Agent S0-A — AylaUrlBuilder + auth contract (PR #1) · 16 SP · #1049 #1050
- **Repo:** ai-bot-platform · **Branch:** `fix/s0a-ayla-url-auth`
- **Allowed:** `apps/integrations/ayla/**`, `apps/integrations/ayla_payments/**`, `config/settings/**`, `docs/architecture/contract-matrix.md`, тесты интеграций.
- **Forbidden:** `apps/booking/**`, `apps/channels/**`, `apps/conversations/**`, `apps/eventbus/**`, `apps/catalog/**`.
- **Prompt:** «Портируй `AylaUrlBuilder` из Ayla `core/ayla_urls.py` в `apps/integrations/ayla/`. Требования: `AYLA_BASE_URL` трактуется host-only; builder сам вставляет `/api/v1`, отвергает scheme-in-path и двойной префикс, нормализует trailing slash. Проведи все 5 клиентов (booking/nutrition/profile/recommendations/user_proxy + ayla_payments) через builder — убери ручные f-строки. Унифицируй s2s-auth: Bearer на `AYLA_INTERNAL_API_TOKEN`; для nutrition — `X-Service-Token` с секретом, равным Ayla `NUTRITION_SERVICE_TOKEN` (переименовать переменную или задокументировать invariant). `AYLA_SERVICE_TOKEN` не существует у Ayla — удалить ссылки. Обнови `contract-matrix.md`.»
- **DoD:** нет `f"{AYLA_BASE_URL}/api..."` в `integrations/ayla`; все клиенты через builder; все Bearer'ы = `AYLA_INTERNAL_API_TOKEN` кроме nutrition; `contract-matrix.md` обновлён.
- **Tests:** unit на builder (host-only, double-prefix reject, trailing slash); проверка, что каждый клиент строит ожидаемый путь.
- **Risk:** MED — трогает все клиенты сразу; митигируется тем, что booking_client уже эталон (не менять контракт, только источник URL).

### Agent S0-B — client path/token fixes (PR #2) · 9 SP · #978 #1048 #1050
- **Repo:** ai-bot-platform · **Branch:** `fix/s0b-client-path-auth`
- **Allowed:** `apps/integrations/ayla/profile_client.py`, `apps/integrations/ayla/recommendations_client.py`, `apps/integrations/ayla/nutrition_client.py`, тесты.
- **Forbidden:** `apps/booking/**`, `apps/channels/**`, `apps/eventbus/**`, `apps/catalog/**`.
- **Prompt:** «Почини `profile_client`: путь → `/api/v1/internal/users/{user_id}/`, токен → `AYLA_INTERNAL_API_TOKEN` (#978 + комментарий про токен). Почини `recommendations_client`: путь → `/api/v1/internal/me/catalog/recommendations/`, токен → `AYLA_INTERNAL_API_TOKEN` (X-External-User-ID уже шлётся), добавь circuit breaker. Вырави nutrition-секрет (#1050). Используй `AylaUrlBuilder` из S0-A (координация ветки).»
- **DoD:** оба клиента резолвятся 200 против route-table Ayla (или мок); recs имеет circuit breaker.
- **Tests:** мок Ayla-роута; проверка заголовков auth + пути.
- **Risk:** LOW. Зависит от S0-A (builder) — мержить после/поверх.

### Agent S0-C — contract tests (PR #3) · 11 SP
- **Repo:** ai-bot-platform (+Ayla route dump) · **Branch:** `test/s0c-contract-tests`
- **Allowed:** `apps/integrations/ayla/tests/**`, `docs/architecture/**`, CI-конфиг.
- **Prompt:** «Собери контракт-тест, диффящий пути+методы+auth всех Ayla-клиентов против route-table/OpenAPI Ayla. Падает при любом расхождении путь/метод/токен. Включи в CI.»
- **DoD:** тест в CI зелёный и ловит искусственное расхождение.
- **Risk:** LOW.

### Agent S0.5 — event_id compatibility (PR #4) · 13 SP · #1058 #946
- **Repo:** ai-bot-platform (+Ayla если ULID) · **Branch:** `fix/s05-event-id-width`
- **Allowed:** `apps/eventbus/**`, тесты.
- **Forbidden:** `apps/channels/**`, `apps/admin_api/**`, `apps/integrations/**`.
- **Prompt:** «Расширь `event_id` в `IngestDedupe`/`IngestDLQ`/`HandlerFailureTracker` до `max_length=36` (миграция) ИЛИ согласуй ULID с Ayla — выбери минимально рискованное (Ayla эмитит `uuid4()` 36 симв., так что расширение колонок предпочтительно). Добавь валидацию длины в `ingest_envelope` (fail-fast → DLQ вместо 500). Проверь allowlist: `booking.no_show` и `tenant.relationship.revoked` эмитятся Ayla, но без consumer'а — держи вне external-delivery allowlist или добавь consumer (#946).»
- **DoD:** реальное Ayla-событие (UUID-36) проходит dedupe без DataError; регресс-тест на длину; эмитируемые-без-consumer'а топики не в allowlist.
- **Tests:** dedupe/DLQ/failure-tracker с 36-симв. id; тест на невалидную длину → DLQ.
- **Risk:** MED — миграция на проде (данных cross-service пока нет, доставка OFF → безопасно сейчас).

### Agent S1 — global safety/consent/handoff (PR #5) · 26 SP · #1046 #1047 #1053
- **Repo:** ai-bot-platform · **Branch:** `feat/s1-global-safety-consent`
- **Allowed:** `apps/channels/max/**`, `apps/orchestrator/**`, `apps/conversations/**`, `apps/handoff/**`, `apps/consent/**`, тесты.
- **Forbidden:** `apps/catalog/**`, `apps/eventbus/**`, `apps/booking/services/create.py`, `apps/integrations/**`.
- **Prompt:** «Проведи `handle_global_max_event` через consent-гейт (Variant A soft gate, #1046: гейтим только память G2 + проактив, разрешаем discovery/chat/one-off) и safety pre_check ДО discovery. Реализуй `apps/channels/max/global_onboarding.py` (`needs_onboarding`, `run_onboarding_turn` поверх WelcomeSkill, тексты `GLOBAL_WELCOME_TEXT`/`GLOBAL_S5` под маркетплейс) за флагом `GLOBAL_BOT_ONBOARDING`. Обработай `SkillResult.should_handoff` в `handle_max_event` И глобальном хендлере → `apps/handoff/services.py::create_admin_task` + заглуши бота. Реши судьбу `pipeline.turn()`: портируй safety pre_check + should_handoff в оба хендлера (или вкрути turn()), убери дрейф. Не ломай старый per-tenant путь (регресс).»
- **DoD:** global path вызывает consent-гейт и safety pre_check; should_handoff → AdminTask + бот молчит при HUMAN_HANDOFF; `current_tenant() is None` во всём онбординге; регресс per-tenant пути.
- **Tests:** suicide/red-flag → handoff; complaint; «оператор»; booking failure → AdminTask; consent truth-table; повторный consent идемпотентен.
- **Risk:** HIGH (P0 safety, доверие). Один стрим, тщательное ревью. **FE (ShiroPy) параллельно:** consent/welcome UI — ревьюим.

---

## ВОЛНА 2 — booking + catalog bridge (после Волны 1)

### Agent S2 — Booking via Ayla REST · 24 SP · #1016 #1051 · Ayla #203
- **Branch:** `feat/s2-booking-ayla-rest` · **Allowed:** `apps/booking/**`, `apps/bookings/**`, `apps/integrations/ayla/booking_client.py`, тесты (+Ayla `appointments/internal_api.py` для #203). **Forbidden:** `apps/catalog/**`, `apps/channels/max/**`, `apps/eventbus/**`.
- **Кратко:** slots service_id fix (#1051); server-side idempotency cancel/reschedule (Ayla #203); auto-provision `ayla_user_id`; RemoteBookingProxy consistency; flip-план; E2E confirm→Ayla REST→proxy.
- **DoD:** E2E-тест booking через Ayla REST без YClients/локальной canonical-записи; нет двойного бронирования с walk-in.

### Agent S3 — Catalog bridge / rebuild · 18 SP · #1044 #1052 · Ayla #200
- **Branch:** `feat/s3-catalog-rebuild` · **Allowed:** `apps/catalog/**`, `apps/orchestrator/discovery.py`, тесты (+Ayla `services/`). **Forbidden:** `apps/booking/services/create.py`, `apps/eventbus/**`.
- **Кратко:** чистый ребилд каталога (4-слойная модель + YClientsMapping, Ayla #200); заполнять `ayla_service_id`/stable-id; убрать дубль поля (#1052); canonical-дом `requires_health_check`; coverage-check.
- **DoD:** sync/rebuild из Ayla (не mysite); `ayla_service_id` coverage ≥ порога; health-grounding читает canonical.

---

## ВОЛНА 3 — marketplace-light (после S2 + S3)

### Agent S4 — умная дискавери (фазовый) · pilot ≈29 SP · #1018 #1020
- **Branch:** `feat/s4-marketplace-light` · **Allowed:** `apps/marketplace/**`, `apps/orchestrator/discovery.py`, `apps/integrations/ayla/recommendations_client.py`, `apps/catalog/**` (только `review_count`), тесты. **Forbidden:** `apps/booking/services/create.py`, `apps/eventbus/**`.
- **Развилка (в prompt):** персонализация/личная история = post-pilot (конфликт с consent #1046 + зависит от G2). Пилот = relevance+trust+geo+goal/price, БЕЗ личной истории.
- **Фаза 1 (в пилот):** `review_count` в mirror (S4.0, prereq) → синоним-recall → Bayesian trust-score → trust-floor → diversity ≤2/салон → reasoning-шаблон → fallback пустого результата.
- **Фаза 2 (в пилот):** единая функция скоринга; goal/price через `MasterServiceOffering→CatalogService.goals/price_from`; `show_masters` (goal/price_max/sort); нить recommendation→slots→booking. **⚠️ КАЖДОЕ новое DTO-поле = ручной `_to_card` + пин `test_dto` + MKT1-линт (не протащить коммерческое поле cross-tenant).**
- **Фаза 3 (fast-follow):** availability-буст только для топ-N + кэш слотов; availability в reasoning.
- **Фаза 4 (post-pilot):** 3 слоя (Твои/Ayla/Исследовать); персональный буст (память+согласие); кросс-тенантная история (G2); ИИ-reasoning.
- **MAX-огр.:** нет carousel → текст+кнопки ≤29 рядов; нет geolocation → гео по городу-строке.
- **DoD (пилот):** «хочу массаж завтра» → ранжировано (trust+geo+goal/price, без личной истории) → слоты → booking в Ayla; MKT1 green (нет утечки коммерческих полей).

---

## Deferred / буфер (W6+ или post-pilot)
S05.4 retention beat (#1056) · S05.5 double-contact dispatcher (#1057) · S1.6 de-drift handlers (#1053) · S1.7 ConsentRecord→memory (#1054) · S1.8 DOB endpoint (Ayla #202) · G.6 G-Notify · S4 Фаза 3 (availability) · S4 Фаза 4 (персонализация, 3 слоя) — зависит от G2.
**Arch ACK #1055 — РЕШЕНО (ACK latent, Вариант B):** declared prefs = Ayla `users.UserPersonalContext` (не трогать), inferred memory = bot. Pre-pilot разрешён только cross-ref docstring в bot-модели (ACK.1). Rename/migrate/unified — post-pilot MEM (MEM-1/2/3).
