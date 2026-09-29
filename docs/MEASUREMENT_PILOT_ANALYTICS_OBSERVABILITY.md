# Замер матрицы готовности пилота — пп. 19 / 30 / 31

**Предмет:** идентичность рекомендации (п.19), аналитика и атрибуция (п.30),
наблюдаемость (п.31).
**Режим:** MEASURE-FIRST, read-only. Ничего не чинилось, не коммитилось.
**Дата замера:** 09.09.2026.

---

## 1. Базы замера

| repo | ветка | фактический SHA (сверен 09.09.2026) | чем снято |
|---|---|---|---|
| `ai-bot-platform` | `origin/dev` | `b1a119bdfb26765bc75ce35dfbf1dd82227183d0` | `git show <ref>:<path>`, `git grep <pat> origin/dev` |
| `djangoproject-catalog` | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | `git show <ref>:<path>`, `git grep <pat> origin/dev` |
| `ayla-ai-core` | — | не открывался (вне предмета) | — |
| `ayla-knowledge` | — | не открывался (вне предмета) | — |

SHA совпали с общим сводом, никуда не уехали.
Рабочий чекаут `ai-bot-platform` стоит на чужой ветке
`feat/recommendation-boundary-client` (`fd6f4e87…`) — **весь замер снят через
`git show`/`git grep` по `origin/dev`**, рабочее дерево не читалось.

Корень: `C:/Users/user/PycharmProjects/Ayla/`.

---

## 2. Executive verdict (по строке на вопрос)

**п.19 — RECOMMENDATION LIFECYCLE / recommendation_id**

1. `recommendation_id` в коде **не существует ни в одном из двух репозиториев** — 0 попаданий вне `docs/`; сам контракт резолвера в разделе «Code reality» перечисляет его среди отсутствующего.
2. Единственная идентичность выдачи — `decision_id` (`str(uuid.uuid4())`), рождается в `djangoproject-catalog:recommendation/_pipeline.py:119`, кладётся в тело HTTP-ответа и **нигде не сохраняется**: модели `Recommendation` нет, записи в БД нет, события нет — идентичность умирает вместе с ответом.
3. `decision_id` живёт **только на ручке `POST /api/v1/internal/recommendation/resolve/`**, а живая полка ходит на другую ручку — `POST /internal/me/catalog/recommendations/`, ответ которой (`layer_1_your_places` / `layer_2_ayla_picks{items,reason_codes}` / `layer_3_explore`) **не содержит вообще никакого идентификатора выдачи**.
4. Клиент, который умеет читать `decision_id` (`ai-bot-platform:apps/integrations/ayla/recommendation_resolver_client.py::resolve_recommendation`), **не имеет ни одного production-caller'а** — `DEAD_CODE`.
5. По факту брони **нельзя** узнать, какой рекомендацией она вызвана. `PendingBookingIntent` в боте — это НЕ доменный объект намерения, а Redis-черновик на 10 минут (`apps/miniapp_api/pending_intent.py`), и его белый список полей — `master_id, service_id, slot_iso, price_quoted, note, loyalty_apply, entry_point` — **не содержит ни `recommendation_id`, ни `decision_id`**.
6. Единственная существующая «AI→бронь» связь — эвристика близости по времени: `apps/observability/ai_metrics.py::_correlate_task_success` считает ход успешным, если `Conversation.last_booking_at` попала в 60 минут после хода; в поле с именем `booking_event_id` при этом записывается **id разговора**, а не брони (строка 260).
7. Из пяти состояний (`presented / engaged / booked / completed / liked`) существуют **два и оба не про рекомендацию**: `booked` (`booking.created`) и `completed` (`booking.completed`). `presented`, `engaged`, `liked` — MISSING.

**п.30 — ANALYTICS / ATTRIBUTION**

8. Реестров событий **два, и они не пересекаются**: `ai-bot-platform:apps/events/vocabulary.py::CANONICAL_EVENTS` (65 имён) и `djangoproject-catalog:analytics/event_catalogue.py::EVENT_NAMES` (39 имён). Ни одна строка бота не пишет в `analytics.AnalyticsEvent`, и наоборот.
9. Из шестнадцати требуемых вещей: **EXISTS — 3**, **PARTIAL — 6**, **MISSING — 7** (таблица в §4.2).
10. `goal_selected` подтверждён замером: `djangoproject-catalog:goals/api.py:107 _emit_goal_selected` пишет `AnalyticsEvent` с `{goal_key, has_text, source_channel}`; дословный `goal_text` действительно не уходит — в полезной нагрузке стоит `has_text: bool`.
11. `recommendation_shown` **зарегистрирован в белом списке каталога и не испускается ни одной строкой кода** — комментарий рядом с константой сам это признаёт («точка подключения — существующая ручка рекомендаций»). Имя без испускателя.
12. Три канонических имени бота — `conversation_started`, `message_received`, `intent_classified` — **не испускаются ни одной строкой** вне словаря. `DEAD_CODE` в реестре.
13. События оседают в **пяти разных таблицах Postgres** и ни в одной шине: `apps.events.Event`, `apps.audit.AuditLog`, `apps.eventbus.DomainEvent` + `IngestDLQ`, `apps.observability.AIRequestMetric` (бот) и `analytics.AnalyticsEvent` (каталог).
14. Внешнего потребителя нет: `settings.EVENT_FANOUTS` по умолчанию `["apps.events.fanout.NoopFanout"]`, а `MixpanelFanout` / `GA4Fanout` / `WarehouseFanout` — скелеты, чьи `send()` только пишут `logger.debug(...skipped (Phase 1 will implement))`.
15. Читателей у `apps.events.Event` в production **нет**: единственные потребители — read-only Django-admin и management-команда учений `d2_flood_drill`. BI-дашборда над `analytics.AnalyticsEvent` в репозитории тоже нет (даже `admin.py` для неё не зарегистрирован).
16. DLQ есть **только на входящей шине** Ayla→бот (`apps.eventbus.IngestDLQ`, есть чистильщик в beat). У аналитической шины `apps.events` DLQ **нет вообще**: `emit()` глотает исключение и пишет `logger.exception` — потерянное событие исчезает молча.

**п.31 — OBSERVABILITY**

17. **Главный вопрос владельца: расследовать один конкретный путь, не читая сырые логи, — ЧАСТИЧНО МОЖНО, но только на канале MAX и только 30 дней.** `apps/replay/` пишет `ReplayTrace` с редактированными `pipeline_steps` по `trace_id`, `REPLAY_SAMPLE_RATE_PROD` по умолчанию `1.0`, поиск по `trace_id` есть в Django-admin (`apps/replay/admin.py:28`), retention `REPLAY_RETENTION_DAYS=30`. То же по `trace_id` ищется в `EventAdmin` (`apps/events/admin.py:20`).
18. **Но: точек захвата две — `apps/channels/max/handler.py:619,621` и `apps/orchestrator/pipeline.py:1421`. Путь через Mini App API (полка, создание брони, резолвер) не захватывается вообще.**
19. Сквозной `correlation_id`/`trace_id` **не переживает границу бот↔Ayla**. Ни один из 16 клиентов `apps/integrations/ayla/*.py` не ставит трассировочный заголовок: заголовки везде ровно `Authorization` + `X-External-User-ID` + `Accept` + `Content-Type`.
20. Приёмник на той стороне при этом есть и простаивает: `djangoproject-catalog:users/middleware.py::RequestIDMiddleware` (`HEADER = "X-Request-ID"`) **уважает входящий заголовок**, если его прислать. Никто не присылает.
21. В обратную сторону `correlation_id` есть, но он **чужой**: `apps.eventbus.ingest_envelope` требует поля `correlation_id`, и оно рождается на стороне Ayla (`uuid.uuid4()` в `appointments/.../cancel_reschedule_service.py:353`), с `trace_id` бота никак не связано.
22. Внутри бота живут **два разных trace_id**: в строках `Event`/`AIRequestMetric` лежит ContextVar-`trace_id` из `apps.tenancy.context` (рождается в ingress), а в JSON-поле `trace_id` каждой строки лога — **OTel span id** (`apps/observability/logging.py::ContextFilter._read_otel_ids`). Это разные значения; склеиваются они только там, где разработчик вручную дописал `trace=%s` в текст сообщения.
23. Логи структурные и в боте, и в каталоге: `apps/observability/logging.py::JsonFormatter` (ts/level/logger/msg/tenant_id/trace_id/span_id/pipeline_step/extra); каталог — `pythonjsonlogger` при `LOG_FORMAT=json`, `prod.py:192` его включает. Уровни: бот — root `INFO` жёстко, без переменной окружения; каталог — `LOG_LEVEL` из окружения.
24. Персональные данные в логах: у бота есть редактор `apps/observability/pii_filter.py::PIIRedactingFilter` на всех постоянных приёмниках — телефон РФ, email, карта по Луну. **Имена он не редактирует сознательно, медицинские данные не редактирует вообще** (в списке паттернов их нет). **У каталога PII-фильтра логов нет ни одного** — только `RequestIDFilter`.
25. Конкретные строки, где в лог попадает пользовательский ввод (значения не приводятся): `apps/bookings/callbacks.py:339` и `:674` (`text=%r`), `apps/orchestrator/discovery.py:1972` (`text=%r`, первые 60 символов callback-текста), `apps/orchestrator/visits.py:858` (`raw=%r`), `apps/skills/booking/skill.py:634,716,726,1831` (`raw=%r` payload'ов кнопок), `apps/promptreg/cache.py:283` (`data=%s`). Это payload'ы нажатий, а не свободный текст, но PII-фильтр по смыслу их не закрывает — он ловит только телефон/почту/карту.
26. Приватность-риск, названный отдельно: `djangoproject-catalog:djangoProject/settings/base.py:1119 _sentry_before_send` — **пустышка, `return event`**, и это в репозитории, где `users`-слой хранит `contraindication` / `symptom`. Единственная защита — `send_default_pii=False`.
27. Health-ручки есть на обеих сторонах: бот — `GET /healthz/` (безусловные 200) и `GET /readyz/` (postgres/redis/chromadb/minio + `pipeline_health`, 503 при отказе), плюс `/admin/health/`; каталог — `GET /api/v1/health/` (БД+кэш) и `/api/v1/health/ready/` (+непринятые миграции).
28. Алерты есть **только у бота**: `apps/observability/alerting.py::page()` (Telegram + Sentry, дедуп 5 мин). Живых вызовов ровно **три**: `apps/catalog/tasks.py:295` (устаревшая синхронизация каталога), `apps/observability/tasks.py:287` (пост-флип нарушения), `apps/observability/ai_metrics.py:767` (пороги AI-качества). У каталога модуля алертинга нет вообще.
29. **Два из трёх алертов не могут сработать.** `CELERY_BEAT_SCHEDULE` содержит `compute_shadow_delta`, но **не содержит** ни `apps.observability.tasks.aggregate_ai_metrics_daily`, ни `monitor_post_flip_violations`. У `aggregate_ai_metrics_daily` вообще **ноль вызывающих** во всём репозитории — `DEAD_CODE`.
30. Следствие: дашборд `/admin/observability/ai-quality/` читает `AIDailyMetricSummary`, которую **на пилоте никто не заполняет**, и корреляция «ход→бронь» (`_correlate_task_success`) **никогда не выполняется**.
31. Метрик как метрик (Prometheus/StatsD) нет ни в одном репозитории — 0 попаданий на `prometheus|Counter(|Histogram(`. Есть OTel-трассы: `apps/observability/otel.py`, семплирование 5 %, экспортёр включается только при непустом `OTEL_EXPORTER_OTLP_ENDPOINT` (умолчание — пусто, то есть no-op).
32. Необработанное исключение **внутри конвейера** — человек ответ получает: `apps/orchestrator/pipeline.py:559` внешняя граница, `_FALLBACK_ERROR` («Извините, что-то пошло не так…») + `emit("pipeline_error", {trace_id, error[:500]})`.
33. Необработанное исключение **в обработчике потока** — человек получает **молчание**: `apps/workers/consumer.py:166-181` ловит, пишет `logger.exception`, **не делает XACK**, запись остаётся в PEL. Комментарий рядом дословно: «**No automatic DLQ retry is wired**… the same entry will not redeliver until XCLAIM moves it». Оператор увидит это только в логе; жнец `workers.reap_pel` в beat есть и переносит записи в `<stream>:dlq`, но ответа человеку это не возвращает.
34. Необработанное исключение **в Mini App API** — обычный Django-500; ни события, ни алерта, ни replay-следа.

**Одной фразой: наблюдаемость у бота построена честно, но её выходы — дашборд AI-качества, пороговый алерт и единственная связь «ход→бронь» — висят на Celery-задаче, которой нет в расписании и у которой ноль вызывающих; а идентичности рекомендации, по которой атрибуция вообще могла бы существовать, нет ни в одном из двух репозиториев.**

---

## 3. Сводка по классам числом

### CURRENT STATE

| класс | число | что именно |
|---|---|---|
| `EXISTS` | 12 | Event/AuditLog/DomainEvent/AnalyticsEvent/AIRequestMetric как хранилища; `emit()`; JSON-логи; PII-фильтр бота; health-ручки ×2; `page()`; ReplayTrace; IngestDLQ; ContextVar trace_id; `goal_selected`; `booking_created`/`cancelled`/`completed` |
| `PARTIAL` | 9 | safety_result; stale_callback; outbound_blocked; internal error; question_presented/resolved (только personal-context каталога); OTel (5 %, экспортёр по умолчанию выключен); replay (только MAX + pipeline); trace в логах ≠ trace в событиях; catalog Sentry scrubber = no-op |
| `MISSING` | 11 | `recommendation_id`; персистентная `Recommendation`; `recommendation_presented`; `recommendation_engaged`; `no_verified_candidate` как событие; `booking_intent_created`; `question_repeated`; `safety_error`; claim-verification; DLQ аналитической шины; метрики (Prometheus) |
| `DEAD_CODE` | 6 | `resolve_recommendation()` (нет caller'а); `aggregate_ai_metrics_daily` (нет caller'а и нет beat); `monitor_post_flip_violations` (нет beat); `CONVERSATION_STARTED`; `MESSAGE_RECEIVED`; `INTENT_CLASSIFIED` |
| `CONTRADICTS_CANON` | 3 | `booking_source="ai_direct"` захардкожен; `ai_assist_score` = константа от строки источника; `booking_event_id` хранит `conversation_id` |
| `STALE_SPEC` | 2 | `recommendation_shown` в белом списке без испускателя; словарь бота обещает 5 канонических имён, которых никто не пишет |
| `UNKNOWN_NOT_MEASURED` | 5 | живые значения `EVENT_FANOUTS`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `SENTRY_DSN`, `REPLAY_SAMPLE_RATE_PROD`, `LOG_FORMAT` на контуре |

### PILOT IMPACT (моя оценка; решает владелец)

| класс | число |
|---|---|
| `STOP` | 3 |
| `DEGRADED` | 9 |
| `POST_PILOT` | 7 |

`STOP` — (1) отсутствие идентичности рекомендации целиком (п.19 недоказуем ни
на каком объёме данных); (2) незапланированная `aggregate_ai_metrics_daily`
(единственный пороговый алерт AI-качества мёртв, и вместе с ним — вся связь
«ход→бронь»); (3) молчание человеку при исключении в обработчике потока
без авто-редоставки.

### TEST STATUS

| класс | число |
|---|---|
| `UNIT_ONLY` | 8 |
| `CONTRACT_ONLY` | 3 |
| `CROSS_BOUNDARY` | 1 |
| `MISSING` | 9 |
| `E2E_GREEN` | 0 |

---

## 4. Находки

### 4.1 п.19 — идентичность рекомендации

**F-19.1 — `recommendation_id` не существует в коде. `MISSING` / `STOP`.**

`ai-bot-platform` `origin/dev`: `git grep -n "recommendation_id" origin/dev` даёт
**17 попаданий, все в `docs/`** — ноль в `apps/`.
`djangoproject-catalog` `origin/dev`: три попадания, из них два — комментарии,
и одно — необязательный параметр телеметрии:

`djangoproject-catalog:users/personal_context_events.py:72-80`
```python
def emit_context_used(user, *, fields_used: list[str], surface: str,
                      recommendation_id: str | None = None) -> None:
    _emit(user, event_catalogue.CONTEXT_USED_IN_RECOMMENDATION, {
        "fields_used": fields_used,
        "surface": surface,
        "recommendation_id": recommendation_id,
    })
```
Функция **не имеет ни одного вызывающего**
(`git grep -n "emit_context_used" origin/dev` → только определение) —
`DEAD_CODE`. То есть единственное место в двух репозиториях, куда
`recommendation_id` мог бы записаться, никем не вызывается и по умолчанию
записало бы `None`.

Спецификация об этом знает сама —
`ai-bot-platform:docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md:1427`,
раздел «2. Code reality», дословно:

> «Отсутствующее названо отсутствующим: `CanonicalService`/`Capability`,
> `mapping_status`, **`recommendation_id`**, реестр reason codes, `probe`.»

**F-19.2 — `decision_id` есть, но не переживает HTTP-ответ. `PARTIAL` / `DEGRADED`.**

`djangoproject-catalog:recommendation/_pipeline.py:119`
```python
        decision_id=str(uuid.uuid4()),
```
Полный список попаданий по репозиторию
(`git grep -n "decision_id" origin/dev -- '*.py' | grep -v tests`):
`_pipeline.py:119` (создание), `_serializers.py:188` (поле схемы ответа),
`_serializers.py:202` (укладка в тело), `_types.py:510` (поле dataclass).
**Ни одного `objects.create`, ни одной миграции, ни одной строки лога с ним.**
`recommendation/views.py:101` логирует ход резолвера и берёт туда
`request_id`, а `decision_id` — нет.

`djangoproject-catalog:recommendation/_types.py:503-506` — авторский комментарий
подтверждает, что персистенции нет по замыслу:
```python
    Персистенция (`Recommendation`, `recommendation_id`, lineage) —
    авторитет домена (§6.1). То, что на пилоте резолвер и домен в одном
    процессе, этого не отменяет.
```

**F-19.3 — живая полка не отдаёт вообще никакого идентификатора. `MISSING` / `STOP`.**

Ручка, на которую реально ходит Mini App:
`ai-bot-platform:apps/miniapp_api/views.py:2622` → `fetch_recommendations` →
`apps/integrations/ayla/recommendations_client.py` → `POST internal/me/catalog/recommendations/`.

Тело ответа собирается в
`djangoproject-catalog:users/catalog_recommendations_api.py:553-557`:
```python
        return success_response({
            "layer_1_your_places": layer_1,
            "layer_2_ayla_picks": layer_2,
            "layer_3_explore": layer_3,
        })
```
где каждый слой — `{"items": [...], "reason_codes": [...]}` (`:288-291`).
Ни `decision_id`, ни `recommendation_id`, ни любого другого идентификатора
выдачи в ответе нет.

**F-19.4 — единственный клиент, читающий `decision_id`, не вызывается. `DEAD_CODE` / `DEGRADED`.**

`git grep -n "resolve_recommendation" origin/dev -- 'apps/**/*.py'` в
`ai-bot-platform` даёт **определение + 4 вызова из его же тестов**, и ничего
больше. Клиент проверяет наличие `decision_id` в ответе
(`recommendation_resolver_client.py:180`), но полученный `decision_id` никуда
не кладёт — возвращает `body["data"]` целиком, и вызывающего у него нет.
Легаси-клиент это признаёт (`recommendations_client.py:174`): «новый код ходит
через него», — миграция потребителя (T6/DRF-1567, T7/DRF-1568) не сделана.

**F-19.5 — `BookingIntent` в боте — это Redis-черновик, а не доменный объект. `CONTRADICTS_CANON` / `DEGRADED`.**

`ai-bot-platform:apps/miniapp_api/pending_intent.py:82-91` — весь белый список:
```python
_ALLOWED_FIELDS: dict[str, type] = {
    "master_id": str,
    "service_id": str,
    "slot_iso": str,
    "price_quoted": (int, float),
    "note": str,
    "loyalty_apply": bool,
    "entry_point": str,
}
```
TTL 600 секунд (`:65`), ключ `pending_booking_intent:{bot_user_id}` (`:99`),
хранилище — `django.core.cache`. Ни `recommendation_id`, ни `decision_id`.
Поле `entry_point` — единственный след происхождения, и это свободная строка
до 64 символов, а не ссылка на выдачу.

Канон владельца требует цепочку
`Recommendation → PendingBookingIntent(recommendation_id) → Booking`
(`docs/specs/PLAN_ENGINE_DEPENDENCY_MAP.md:47`). В коде существует только
третье звено.

**F-19.6 — реальная атрибуция «ход→бронь» — эвристика 60 минут, и поле лжёт именем. `CONTRADICTS_CANON` / `STOP`.**

`ai-bot-platform:apps/observability/ai_metrics.py:250-261`
```python
        gap = booking_at - metric.created_at
        if timedelta(0) <= gap <= BOOKING_CORRELATION_WINDOW:
            AIRequestMetric.all_tenants.filter(pk=metric.pk).update(
                success_correlated_at=now,
                # Conversation.id used as correlation reference until Ayla
                # exposes a stable booking event UUID via event consumer.
                booking_event_id=metric.conversation_id,
            )
```
Поле называется `booking_event_id` и объявлено в модели как
«If a booking event landed within 60 min of this AI message in the …»
(`apps/observability/models.py:319-323`), а хранит **id разговора**.
Комментарий честен, имя — нет; читатель дашборда получает число, названное
идентификатором брони.

**F-19.7 — `booking_source="ai_direct"` захардкожен, а `ai_assist_score` — константа от строки. `CONTRADICTS_CANON` / `DEGRADED`.**

`ai-bot-platform:apps/booking/services/create.py:321,327,342-343`
```python
                booking_source="ai_direct",
                ...
            ai_assist_score = compute_assist_score(booking_source="ai_direct")
```
Аргумент функции подсчёта — литерал, который писатель сам же и подставил
строкой выше. `compute_billable` (`apps/booking/services/attribution.py:107`)
ветвится по этой же строке. Докстрока модуля (`:19-21`) описывает это как
эвристику: «pure `ai_direct` booking earns 1.00; everything else 0.00».
Балл не измеряет вклад помощника — он повторяет строку, которую написал код,
создавший бронь.

**F-19.8 — пять состояний: существуют два, оба не про рекомендацию. `MISSING` / `STOP`.**

| состояние | есть? | чем подтверждено |
|---|---|---|
| `presented` | **нет** | `recommendation_shown` — имя без испускателя (F-30.7); `wellness.recommendation.shown` живёт только в `docs/design/policies/event-taxonomy.md:181` |
| `engaged` | **нет** | `wellness.recommendation.acted` — только `event-taxonomy.md:182`; в коде 0 попаданий |
| `booked` | есть, но без ссылки на рекомендацию | `apps/booking/services/create.py` `emit(...)`, автопроводка `apps/eventbus/signals.py:107` |
| `completed` | есть | `apps/bookings/tasks.py:495` `"booking.completed"`, консьюмер `apps/eventbus/consumers/booking.py:1569` |
| `liked` | **нет** | ближайшее — `ImplicitFeedbackSignal` (`apps/observability/models.py:531`), три поведенческих сигнала, к рекомендации не привязанные |

**F-19.9 — `NO_VERIFIED_CANDIDATES` — не состояние системы, а вывод фронта. `PARTIAL` / `DEGRADED`.**

`ai-bot-platform:apps/miniapp/src/lib/customer-booking.ts:340-342`
```ts
  if (decisionCodes.includes(NOT_RECOMMENDABLE_CODE)) return "NO_VERIFIED_CANDIDATES";
  if (excludedCodes.includes(NOT_RECOMMENDABLE_CODE)) return "NO_VERIFIED_CANDIDATES";
```
Имя вычисляется в браузере из reason codes. На сервере ему соответствуют
только счётчики переписи, которые уходят в **строку лога**:
`djangoproject-catalog:recommendation/_types.py:495-499`
(`MappingCensus.as_log_fields` → `visible= eligible= review_required= unmapped= unknown=`),
печатается в `users/catalog_recommendations_api.py:550`. Ни события, ни строки
в БД. Счётчик у гейта есть — но он существует только в момент, когда его напечатали.

### 4.2 п.30 — шестнадцать требуемых вещей, по списку

Легенда мест: **[bot]** = `ai-bot-platform origin/dev`, **[cat]** = `djangoproject-catalog origin/dev`.

| # | требование | вердикт | имя события | место испускания |
|---|---|---|---|---|
| 1 | `conversation_started` | **MISSING** | `conversation_started` объявлено, не пишется | [bot] `apps/events/vocabulary.py:38` — константа; `git grep '"conversation_started"' origin/dev -- 'apps/**/*.py'` → 0 попаданий вне словаря |
| 2 | `question_presented` | **PARTIAL** | `profile_question_shown` | [cat] `users/personal_context_events.py:53`. Это вопрос о поле профиля (`surface="api"`), а не уточняющий вопрос диалога. Испускателя `emit_question_shown` в production нет — вызываются только `emit_question_answered`/`_skipped` (`users/personal_context_views.py:175,293`). В [bot] аналога нет |
| 3 | `question_resolved` | **PARTIAL** | `profile_question_answered` | [cat] `users/personal_context_views.py:175`. Только personal-context; уточнения диалога [bot] (`CLARIFY_CALLBACK_PREFIX`, `apps/channels/max/handler.py:1306-1310`) не пишут ничего |
| 4 | `question_repeated` | **MISSING** | — | Ближайшее — `profile_question_skipped` со счётчиком `skip_count` ([cat] `personal_context_events.py:65`), но это пропуск, а не повтор. Повторно заданный вопрос ничем не отличается от первого |
| 5 | `safety_result` | **PARTIAL** | `safety_triggered` | [bot] ровно два испускателя: `apps/consent/decorators.py:82` (`reason="consent_denied"`) и `apps/orchestrator/safety/voice_check.py:94` (`reason="forbidden_phrase"`). **Записи «проверка прошла» нет** — событие пишется только при срабатывании; долю безопасных ходов посчитать нечем |
| 6 | `safety_error` | **MISSING** | — | Отказ самой проверки виден только в логе: `apps/orchestrator/safety/outbound.py:293` `safety.outbound.check_failed`, `safety/post_check.py:175` `voice_check_failed`, `post_check.py:107` / `pre_check.py:157` `bad_regex`. Ни события, ни алерта |
| 7 | `recommendation_presented` | **MISSING** | `recommendation_shown` зарегистрировано, не пишется | [cat] `analytics/event_catalogue.py:102` + в `EVENT_NAMES` (`:124`). `git grep "RECOMMENDATION_SHOWN\|recommendation_shown" origin/dev` → **3 попадания, все внутри самого файла реестра** |
| 8 | `recommendation_engaged` | **MISSING** | — | 0 попаданий в обоих репозиториях вне `docs/` |
| 9 | `no_verified_candidate` | **MISSING** (как событие) | — | См. F-19.9: имя выводится в TS, счётчики уходят в строку лога |
| 10 | `booking_intent_created` | **MISSING** | — | `PendingBookingIntent` — кэш без события (F-19.5). `git grep "booking_intent_created" origin/dev` → 0 |
| 11 | `booking_created` | **EXISTS** | `booking.created` (домен) + `booking_created` (моб.) | [bot] `apps/booking/services/create.py` `emit(...)`, автопроводка `apps/eventbus/signals.py:107`; несёт `correlation_id` (`create.py:361`). [cat] `analytics/event_catalogue.py:31` — испускает мобильное приложение через `POST /api/v1/analytics/event/` |
| 12 | `booking_cancelled` | **EXISTS** | `booking.cancelled`, `marketplace.visit.cancelled` | [bot] `apps/booking/services/transitions.py` `emit(EVENT_CANCELLED, ...)`; отдельное имя для отмены с карточки — `apps/events/vocabulary.py` `VISIT_CANCELLED`. [cat] `booking_cancelled` в белом списке |
| 13 | `booking_completed` | **EXISTS** | `booking.completed` | [bot] производитель `apps/bookings/tasks.py:495` (beat `detect_completed_bookings`, каждые 30 мин), консьюмер `apps/eventbus/consumers/booking.py:1569`. [cat] `booking_completed` в белом списке |
| 14 | `stale_callback` | **PARTIAL** | `bookings.gate.expired` | [bot] **один** аудит-слаг: `apps/bookings/callbacks.py:288` объявлен, `:739` записан через `write_audit` — это AuditLog, не аналитическая шина. Три других устаревших нажатия следа не оставляют вовсе: `CLARIFY_STALE_TEXT` (`apps/channels/max/handler.py:1313,1832`), `CATALOG_STALE_CARD_TEXT` (`:1847`), `STALE_TAP_TEXT` (`:1676`) — человеку показывается текст, в телеметрию не пишется ничего |
| 15 | `outbound_revise` / `blocked` / claim | **PARTIAL** | `safety.outbound_blocked` | [bot] `apps/orchestrator/safety/gate.py:212-224` — есть, но **вне канонического словаря**, то есть `emit()` на каждом таком событии пишет `logger.warning("events.emit.non_canonical …")`. **REVISE события не имеет**: `apps/orchestrator/composer.py:112-127` выставляет флаг `safety_revised`, который дальше попадает только в replay-шаг (`pipeline.py:1414`). **Claim-verification в коде отсутствует как понятие** — `git grep "outbound_claim\|claim_check\|unverified_claim" origin/dev` → 0 |
| 16 | internal error / `correlation_id` | **PARTIAL** | `pipeline_error` | [bot] `apps/orchestrator/pipeline.py:1431-1434`, строковый литерал, **вне канонического словаря** (константа `PIPELINE_ERROR` объявлена в `vocabulary.py:50` и не используется). Несёт `{trace_id, error[:500]}`. Исключение в обработчике потока события не порождает вообще (F-31.7) |

Итого: **EXISTS 3, PARTIAL 6, MISSING 7.**

**F-30.7 — реестры событий целиком.**

[cat] `analytics/event_catalogue.py` — 39 имён в `EVENT_NAMES`:
booking_viewed / created / cancelled / rescheduled / completed;
ai_chat_opened / ai_chat_message_sent / ai_action_shown / confirmed / rejected /
ai_clarification_answered; food_scan_taken / confirmed / food_log_added_manual /
water_logged / daily_summary_viewed; personal_context_field_set / cleared /
context_question_skipped; profile_question_shown / answered / skipped /
context_used_in_recommendation; app_opened / onboarding_started / completed /
push_received / tapped; pro_dashboard_viewed / pro_booking_viewed / actioned;
search_performed / specialist_viewed / favorited / unfavorited;
personal_data_deleted; external_identity_bound; **goal_selected**;
**recommendation_shown**.

**Чего в реестре каталога нет:** `recommendation_presented`,
`recommendation_engaged`, `no_verified_candidate`, `booking_intent_created`,
`question_repeated`, `safety_result`, `safety_error`, `stale_callback`,
`conversation_started`.

[bot] `apps/events/vocabulary.py::CANONICAL_EVENTS` — 65 имён, из них
жизненный цикл диалога представлен тремя (`conversation_started`,
`message_sent`, `message_received`), а испускается **одно**
(`MESSAGE_SENT`, `apps/orchestrator/pipeline.py:1125`). Основная масса словаря —
администрирование мастеров (`master.*`, 17 имён), внутренний чат
(`internal_chat.*`, 6 имён), лояльность и бронирование.

**Чего в словаре бота нет:** ни одного имени про рекомендацию.

**F-30.8 — валидация имён не отбрасывает, а предупреждает. `PARTIAL` / `POST_PILOT`.**

[bot] `apps/events/services.py:108-116`:
```python
    if event_name not in CANONICAL_EVENTS:
        logger.warning(
            "events.emit.non_canonical event=%s tenant=%s trace=%s", ...
        )
```
Строка всё равно вставляется. Практическое следствие: `safety.outbound_blocked`
и `pipeline_error` — два события, важные для пилота, — на каждом срабатывании
пишут в лог предупреждение о самих себе. У каталога поведение противоположное:
неизвестное имя → HTTP 400 `UNKNOWN_EVENT_NAME` (`analytics/views.py:86-92`).

**F-30.9 — куда оседают события. `EXISTS` / информационно.**

| хранилище | repo | что | кто читает |
|---|---|---|---|
| `apps.events.Event` | bot | аналитическая шина: `event_name/properties/distinct_id/dialog_id/trace_id` | **никто в production**: read-only `EventAdmin` (`apps/events/admin.py`) + `d2_flood_drill.py:315` |
| `apps.audit.AuditLog` | bot | `action/target/payload`, retention-задача в beat | Django-admin |
| `apps.eventbus.DomainEvent` | bot | междоменные события с `correlation_id`/`causation_id` | `apps/eventbus/dispatcher.py`, `consumers/*` |
| `apps.eventbus.IngestDLQ` | bot | **DLQ входящих событий Ayla→бот**, чистильщик `eventbus.cleanup_ingest_dlq` в beat | оператор через admin |
| `apps.observability.AIRequestMetric` | bot | одна строка на ход: `request_id`(=trace), intent, skill, latency, tokens, cost, outcome | агрегатор, **который не запущен** (F-31.5); admin-класса у модели нет |
| `analytics.AnalyticsEvent` | cat | приём от мобильных + серверные emit'ы | BI «схема-на-чтении» (Metabase/Superset по докстроке) — **в репозитории ни подключения, ни дашборда, ни admin-регистрации** |

**F-30.10 — внешней шины нет, DLQ у аналитики нет. `MISSING` / `DEGRADED`.**

[bot] `config/settings/base.py:500-502`:
```python
EVENT_FANOUTS: list[str] = [
    ... os.environ.get("EVENT_FANOUTS", "apps.events.fanout.NoopFanout").split(",")
```
`apps/events/fanout.py`: `NoopFanout.send` — «Intentionally empty — Phase 0
keeps events in DB only»; `MixpanelFanout` / `GA4Fanout` / `WarehouseFanout` —
`logger.debug("… skipped (Phase 1 will implement)")`.
Живое значение переменной окружения на контуре **не замерено** (см. §7).

DLQ у аналитической шины отсутствует: `emit()` (`apps/events/services.py:139-148`)
на любой ошибке вставки пишет `logger.exception` и продолжает; возвращаемое
`False` игнорируется всеми вызывающими, кроме `apps/workers/subscriber_audit.py:29`.

### 4.3 п.31 — наблюдаемость

**F-31.1 — сквозная корреляция обрывается на границе бот→Ayla. `MISSING` / `STOP`.**

Заголовки исходящих запросов к Ayla, дословно
(`apps/integrations/ayla/recommendation_resolver_client.py:261-267`):
```python
def _headers(external_user_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.AYLA_INTERNAL_API_TOKEN}",
        "X-External-User-ID": external_user_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
```
То же в `goals_client.py:237-242`, `identity_client.py:216`,
`booking_client.py:687-694` (+ опциональный `X-Idempotency-Key`),
`nutrition_client.py` (шесть мест).
`git grep -in "X-Trace\|X-Request-Id\|X-Correlation\|traceparent" origin/dev -- 'apps/**/*.py'`
даёт **ровно одно попадание на весь репозиторий**, и оно на **входе**:
`apps/miniapp_api/views.py:1131 correlation_id = request.headers.get("X-Correlation-Id", "")`.

Приёмник на той стороне готов и простаивает —
`djangoproject-catalog:users/middleware.py:30-53`:
```python
    """Stamp every request with an X-Request-ID and echo it in the response.
    If the client sent ``X-Request-ID`` (incoming gateway, mobile app, retry …
    HEADER = "X-Request-ID"
        request_id = incoming or uuid.uuid4().hex
```
Механизм существует на одном конце и не используется другим. Это не
«технически невозможно» — это не подключено.

**F-31.2 — тот `X-Correlation-Id`, который бот принимает, ему никто не шлёт. `DEAD_CODE` / `DEGRADED`.**

`apps/miniapp_api/views.py:1131` читает заголовок и кладёт значение в
`create_customer_booking(correlation_id=…)` → в properties события
`booking.created` (`apps/booking/services/create.py:361`).
`git grep -in "correlation" origin/dev -- 'apps/miniapp/src/**'` → **пусто**:
Mini App этот заголовок не отправляет. Поле всегда `""`.

**F-31.3 — в боте два разных trace_id. `PARTIAL` / `DEGRADED`.**

*Событийный* — ContextVar: `apps/tenancy/context.py:66 _TRACE_ID`, рождается
в ingress (`apps/ingress/streams.py:125` — `current_trace_id() or str(uuid.uuid4())`),
переносится в запись Redis Stream, воркер входит в `trace_id_scope`
(`apps/workers/consumer.py:155`, `apps/workers/base.py:348`); из него `emit()`
берёт `Event.trace_id` и из него же — `AIRequestMetric.request_id`.

*Логовый* — OTel: `apps/observability/logging.py::ContextFilter.filter`
проставляет `trace_id`/`span_id` из `apps.observability.otel.get_current_span_ids()`.

Это **разные значения**. Строка лога, у которой в JSON-поле `trace_id` стоит
OTel-id, и строка `Event` с ContextVar-`trace_id` не соединяются автоматически —
только там, где автор вручную дописал `trace=%s` в текст сообщения
(таких мест много, но это дисциплина, а не механизм).

**F-31.4 — расследовать один путь без сырых логов частично можно: ReplayTrace. `EXISTS` / `POST_PILOT`.**

`apps/replay/models.py:44 ReplayTrace` — `trace_id`, `pipeline_steps` (JSON:
«input message, intent decision, …»), `redacted=True`, `redaction_method`,
`expires_at`. Захват: `apps/channels/max/handler.py:619,621` и
`apps/orchestrator/pipeline.py:1421`.
Семплирование: `config/settings/base.py:466 REPLAY_SAMPLE_RATE_PROD = 1.0`
(умолчание — каждый ход). Retention: `REPLAY_RETENTION_DAYS = 30` (`:471`),
чистильщик в beat (`cleanup_expired_replay_traces`).
Поиск оператором: `apps/replay/admin.py:28 search_fields = ("trace_id", "id")`,
плюс `apps/events/admin.py:20 search_fields = ("event_type", "trace_id")`.

Ограничения, из-за которых ответ «частично»:
(а) захвата на пути Mini App API нет — полка, резолвер и создание брони
из Mini App следа в replay не оставляют;
(б) поиск идёт по ContextVar-`trace_id`, а структурное поле `trace_id` в
логах несёт OTel-id (F-31.3);
(в) у `AIRequestMetric` **admin-класса нет вообще** — в `apps/observability/`
файла `admin.py` не существует, то есть построчная телеметрия хода оператору
из UI недоступна.

**F-31.5 — выходы наблюдаемости висят на незапланированной задаче. `DEAD_CODE` / `STOP`.**

`config/settings/base.py:1198` `CELERY_BEAT_SCHEDULE` содержит 26 записей;
`git show origin/dev:config/settings/base.py | sed -n '1198,1560p' | grep '^    "'`
перечисляет их полностью. Из `apps.observability` там **только**
`compute_shadow_delta_daily`.

- `apps.observability.tasks.aggregate_ai_metrics_daily` (`tasks.py:341`):
  `git grep -rn "aggregate_ai_metrics_daily" origin/dev | grep -v tests` →
  **одно попадание — само определение**. Ни beat-записи, ни `apply_async`,
  ни вызова из другой задачи. `DEAD_CODE`.
- `apps.observability.tasks.monitor_post_flip_violations` (`tasks.py:169`):
  вызывает сам себя на перепроверку (`:250`) и упомянут в раннбуках
  (`docs/runbooks/strict-scope-flip.md:161`), но **в beat не зарегистрирован**.

Последствия, каждое проверено отдельно:
1. `AIDailyMetricSummary` никем не заполняется → дашборд
   `/admin/observability/ai-quality/` (`views_ai_quality.py:62`) на пилоте пуст.
2. `_alert_breaches` → `page("warning", …)` (`ai_metrics.py:767`) недостижим →
   **порогового алерта по качеству AI на пилоте нет**.
3. `_correlate_task_success` (`ai_metrics.py:211`) вызывается только из
   `aggregate_daily_metrics` (`:608`) → **единственная связь «ход→бронь»
   никогда не вычисляется**.

**F-31.6 — алертов три, живой один. `PARTIAL` / `DEGRADED`.**

`apps/observability/alerting.py::page()` — Telegram + Sentry, три уровня
(`critical`/`error`/`warning`), дедуп 5 минут через Redis, аудит-строка на
каждый вызов, «best-effort and never raises».
Вызовы вне тестов: `apps/catalog/tasks.py:295` (устаревшая синхронизация,
beat `catalog_sync_staleness_hourly` — **живой**),
`apps/observability/tasks.py:287` (пост-флип — **beat отсутствует**),
`apps/observability/ai_metrics.py:767` (пороги AI — **beat отсутствует**).
Плюс ручная команда `manage.py smoke_alert`.
В `djangoproject-catalog` модуля алертинга нет:
`git grep -rn "def alert\|send_alert\|page("` → 0.

**F-31.7 — исключение в обработчике потока = молчание человеку и никакой авто-редоставки. `EXISTS` (поведение) / `STOP`.**

`apps/workers/consumer.py:166-182`, дословно:
```python
                try:
                    handler(decoded)
                except Exception:  # noqa: BLE001 — handler logged + emitted; consumer continues
                    logger.exception(
                        "workers.handler_raised stream=%s entry_id=%s", ...
                    handler_failed = True

            if handler_failed:
                # Do NOT XACK on failure. Entry stays in PEL for
                # operator escalation (manual XCLAIM / XAUTOCLAIM).
                # **No automatic DLQ retry is wired** as of 2026-05-21
                # — XREADGROUP ">" returns only NEW entries, so the
                # same entry will not redeliver until XCLAIM moves it.
                continue
```
То же обещание в трёх других местах: `apps/channels/handlers.py:46`,
`apps/workers/base.py:143,181`. Смягчающее: жнец `workers.reap_pel`
**в beat есть** и делает XAUTOCLAIM с переносом в `<stream>:dlq` — но это
forensics, а не ответ человеку. Событие при этом не пишется; оператор узнаёт
только из лога.

Для сравнения — внутри конвейера поведение правильное:
`apps/orchestrator/pipeline.py:559` внешняя граница, `:1452`
`reply=ComposedReply(text=_FALLBACK_ERROR, final_send=True)`,
`:1431` `emit("pipeline_error", {...})`.
На поверхности Mini App API — обычный Django-500 без события, алерта и replay-следа.

**F-31.8 — приватность в логах: телефон/почта/карта закрыты, имена и медицина — нет. `PARTIAL` / `DEGRADED`.**

[bot] `config/settings/base.py:2290 LOGGING` — фильтры `pii_redactor` +
`context` на единственном handler'е `console`, форматтер `json`, root `INFO`.
`apps/observability/pii_filter.py` редактирует: телефон РФ, email, карту с
проверкой Луна. Раздел докстроки «What is NOT redacted (deliberately, in this PR)»:
имена (риск ложных срабатываний) и «whitelisted opaque IDs».
**Медицинские данные в списке паттернов отсутствуют вовсе** — а в
`apps/identity/models.py:745-746` живут типы `contraindication` и `symptom`.

[cat] PII-фильтра логов **нет**: `djangoProject/settings/base.py:1149 LOGGING`
содержит единственный фильтр `request_id` (`core/log_filters.py::RequestIDFilter`).
Sentry-скруббер каталога — пустышка (`base.py:1119-1125`):
```python
    def _sentry_before_send(event, hint):
        # … Today: no-op.
        return event
```
Единственная защита — `send_default_pii=False` (`:1131`).

Конкретные строки, где в лог попадает содержимое пользовательского нажатия
(значения не приводятся): `apps/bookings/callbacks.py:339`, `:674`;
`apps/orchestrator/discovery.py:1972`; `apps/orchestrator/visits.py:858`;
`apps/skills/booking/skill.py:634,716,726,1831`; `apps/promptreg/cache.py:283`.

Свободный текст пользователя нигде в лог не пишется — дисциплина выдержана:
`apps/orchestrator/intent_router.py:374` пишет `text_len=%d`, а не текст;
`apps/orchestrator/safety/outbound.py:301` — `categories` + `len(body)`;
`safety/gate.py:214-223` в событие кладёт `categories` + `text_len`, а не сам текст.
`AIRequestMetric` хранит `message_text_length`, а не текст.
`ReplayTrace` — единственное место, где содержимое хода сохраняется, и оно
проходит через `apps/replay/redactor.py` с записью `redaction_method` в строке.

**F-31.9 — health-ручки есть на обеих сторонах. `EXISTS` / —.**

[bot] `apps/orchestrator/views.py::healthz` — безусловные 200 (liveness);
`::readyz` — postgres/redis/chromadb/minio параллельно + `pipeline_health()`,
503 при любом отказе, `{"checks": {...}}` в теле. Плюс
`/admin/health/` (`apps/adminconsole/urls.py`, включает возраст синхронизации каталога).
[cat] `djangoProject/health.py::liveness` (`GET /api/v1/health/`, БД + кэш),
`::readiness` (`/api/v1/health/ready/`, + непринятые миграции), обе с
`{"status","version","timestamp","checks"}` и 503 при отказе.

**F-31.10 — метрик нет; трассы есть, но по умолчанию никуда не уходят. `PARTIAL` / `POST_PILOT`.**

`git grep -rn "prometheus\|Counter(\|Histogram("` — 0 попаданий в обоих репозиториях.
OTel: `apps/observability/otel.py::configure_otel` — ресурс
`service.name=ai-bot-platform`, семплер `ParentBased(TraceIdRatioBased(0.05))`,
экспортёр OTLP/gRPC **только при непустом** `OTEL_EXPORTER_OTLP_ENDPOINT`
(умолчание `""` → no-op). Живое значение на контуре не замерено.
Докстрока сама признаёт: «Does not auto-instrument Django / requests / Celery».

---

## 5. Подтверждённые противоречия (обе стороны дословно)

**П-1. Спецификация обещает `recommendation_id`; код его не имеет — и спецификация же это подтверждает.**

Требование —
`ai-bot-platform:docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md:518`:
> «Каждая **выданная человеку** рекомендация получает `recommendation_id`.»

и `:1166`:
> «Атрибуция записи: `Recommendation → PendingBookingIntent(recommendation_id) → Booking`»

Факт — тот же документ, `:1427` (раздел «2. Code reality», статус «пройден»):
> «Отсутствующее названо отсутствующим: `CanonicalService`/`Capability`,
> `mapping_status`, **`recommendation_id`**, реестр reason codes, `probe`.»

Код: `git grep -n "recommendation_id" origin/dev -- 'apps/'` → **0**.

**П-2. Имя поля обещает идентификатор брони, значение — идентификатор разговора.**

`apps/observability/models.py:319-323`:
> `booking_event_id = models.UUIDField(… help_text="If a booking event landed within 60 min of this AI message in the …")`

`apps/observability/ai_metrics.py:258-260`:
> `# Conversation.id used as correlation reference until Ayla exposes a stable booking event UUID …`
> `booking_event_id=metric.conversation_id,`

**П-3. Реестр каталога объявляет `recommendation_shown` состоявшимся именем; ни одна строка кода его не пишет — и комментарий рядом это признаёт.**

`djangoproject-catalog:analytics/event_catalogue.py:98-102`:
> «`recommendation_shown`: зарегистрировано заранее; точка подключения —
> существующая ручка рекомендаций, поведение которой меняется только
> отдельным решением…»

и оно же в `EVENT_NAMES` (`:124`) — то есть с точки зрения API-контракта имя
валидно и мобильное приложение получит 201, хотя сервер его не пишет никогда.

**П-4. Словарь событий бота объявляет себя «единственным источником истины» для жизненного цикла диалога, а половину объявленного никто не пишет.**

`apps/events/vocabulary.py:11-13`:
> «The constants below are the single source of truth; `emit()` (B3)
> validates incoming names against `CANONICAL_EVENTS`…»

Проверка испускателей: `CONVERSATION_STARTED` — 0, `MESSAGE_RECEIVED` — 0,
`INTENT_CLASSIFIED` — 0 (единственные попадания `intent_classified` — имя
*колонки* `AIRequestMetric`, не события), `PIPELINE_ERROR` — 0 (событие
пишется строковым литералом мимо константы, `pipeline.py:1432`).

**П-5. Механизм сквозной корреляции реализован на приёмной стороне и не используется отправляющей.**

Приёмник — `djangoproject-catalog:users/middleware.py:32`:
> «If the client sent `X-Request-ID` (incoming gateway, mobile app, retry …)»

Отправитель — все 16 клиентов `ai-bot-platform:apps/integrations/ayla/*.py`,
ни в одном из которых нет трассировочного заголовка (F-31.1).

**П-6. Модуль говорит «telemetry must never drop», а потеря телеметрии не имеет ни DLQ, ни счётчика.**

`apps/events/services.py:39-41`:
> «If the resolved `event_name` is not in `CANONICAL_EVENTS`, we **log a
> warning but still insert the row**. The principle: telemetry must never drop.»

`apps/events/services.py:139-148`: при отказе вставки — `logger.exception` и
`inserted = False`; возвращаемое значение игнорируют все вызывающие, кроме
одного (`apps/workers/subscriber_audit.py:29`). DLQ у этой шины нет.

---

## 6. Реальность тестов

### Что покрыто и каким уровнем

| предмет | файл | уровень |
|---|---|---|
| словарь событий бота (константы, иммутабельность, `is_canonical`) | `apps/events/tests/test_vocabulary.py` | `UNIT_ONLY` |
| `emit()` — legacy + канонический конверт, зеркальная запись | `apps/events/tests/test_emit.py`, `test_emit_canonical.py` | `UNIT_ONLY` |
| fanout — реестр, глотание ошибок адаптера | `apps/events/tests/test_fanout.py` | `UNIT_ONLY` |
| схема конверта | `apps/events/tests/test_schema.py`, `test_model_canonical_schema.py` | `UNIT_ONLY` |
| атрибуция брони (`booking.created` + `booking.attribution.assigned`) | `apps/eventbus/tests/test_attribution_signal.py` | `UNIT_ONLY` (обе стороны данных строит один автор — фикстура) |
| DLQ входящей шины, подпись, rate-limit, таймаут | `apps/eventbus/tests/test_handler_exception_dlq.py`, `test_ingest_security.py`, `test_ingest_rate_limit.py`, `test_ingest_timeout.py` | `CONTRACT_ONLY` |
| контрактные фикстуры eventbus | `apps/eventbus/tests/test_contract_fixtures.py` + `tests/fixtures/contracts/MANIFEST.sha256` | `CONTRACT_ONLY` |
| PII-фильтр логов (включая бенчмарк и интеграцию) | `apps/observability/tests/test_pii_filter.py`, `test_pii_filter_integration.py` | `UNIT_ONLY` |
| JSON-форматтер, ContextFilter | `apps/observability/tests/test_logging.py`, `test_context_filter.py` | `UNIT_ONLY` |
| OTel-конфигурация | `apps/observability/tests/test_otel.py` | `UNIT_ONLY` |
| алертинг (дедуп, оба приёмника, деградация) | `apps/observability/tests/test_alerting.py`, `test_smoke_alert_command.py` | `UNIT_ONLY` |
| агрегация AI-метрик и дашборд | `apps/observability/tests/test_ai_aggregation.py`, `test_ai_quality_dashboard.py` | `UNIT_ONLY` — **тестируют функцию, которую production не вызывает** |
| клиент границы резолвера (три исхода) | `apps/integrations/ayla/tests/test_recommendation_resolver_client.py` | `UNIT_ONLY` — **тестируют функцию без production-caller'а** |
| гейт границы рекомендаций | `tests/contracts/test_recommendation_boundary_guard.py` | `CONTRACT_ONLY` |
| приём событий каталога (идемпотентность, `UNKNOWN_EVENT_NAME`) | `djangoproject-catalog:analytics/tests/test_event_endpoint.py` | `UNIT_ONLY` |
| воронка целей, `goal_selected` | `djangoproject-catalog:goals/tests/test_goal_layer.py` | `UNIT_ONLY` |
| e2e Ayla-интеграция | `ai-bot-platform:tests/e2e/test_ayla_integration.py` | `CROSS_BOUNDARY` (единственный) |

### Каких тестов НЕТ — поимённо

1. **Нет теста, что у выданной рекомендации есть идентичность.** Ни в одном
   репозитории нет файла, утверждающего, что ответ полки несёт
   `recommendation_id`/`decision_id`. Проверять нечего: поля нет.
2. **Нет теста атрибуции «рекомендация → бронь».**
   `git grep -rln "recommendation" origin/dev -- 'apps/**/tests/*.py' 'tests/'`
   даёт 10 файлов, и ни один из них не связывает выдачу с созданной бронью.
3. **Нет теста, что `conversation_started` испускается.** Именно поэтому мёртвая
   константа прожила незамеченной: `test_vocabulary.py` проверяет, что имя
   *объявлено*, а не что оно *пишется*. Теста формы «для каждого имени в
   `CANONICAL_EVENTS` существует хотя бы один production-испускатель»
   не существует — а это ровно тот тест, который поймал бы шесть находок сразу.
4. **Нет теста, что `recommendation_shown` испускается** (`djangoproject-catalog`) —
   имя в `EVENT_NAMES` проходит валидацию сериализатора, испускателя нет.
5. **Нет теста, что `trace_id` переживает границу бот↔Ayla.** Ни один тест не
   отправляет запрос к Ayla и не проверяет присутствие корреляционного
   заголовка. `test_recommendation_resolver_client.py` проверяет форму ответа,
   а не заголовки запроса.
6. **Нет теста состава `CELERY_BEAT_SCHEDULE`.**
   `git grep -rln "CELERY_BEAT_SCHEDULE" origin/dev -- 'tests/' 'apps/**/tests/'` → **пусто**.
   Это единственный тест, который поймал бы F-31.5: `test_ai_aggregation.py`
   зелёный, потому что вызывает задачу напрямую, — а расписание её не вызывает никогда.
7. **Нет теста, что `AIDailyMetricSummary` заполняется на живом пути.**
   `test_ai_quality_dashboard.py` строит строки фикстурой — классический случай
   «обе стороны данных построил один автор».
8. **Нет теста, что необработанное исключение в обработчике потока даёт хоть
   какой-то сигнал оператору.** `test_handler_exception_dlq.py` покрывает DLQ
   *входящей* шины eventbus, а не PEL Redis Streams.
9. **Нет теста, что медицинские/чувствительные данные не попадают в логи.**
   `test_pii_filter.py` проверяет телефон/почту/карту — по всем трём фильтр
   работает. Категории `contraindication`/`symptom` не проверяются ничем,
   потому что фильтр их и не пытается ловить.

---

## 7. Что НЕ замерено — честный список

1. **Живые значения переменных окружения на контуре** (снимает окно `ayla-06`):
   `EVENT_FANOUTS`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLE_RATE`,
   `SENTRY_DSN` (обе стороны), `REPLAY_SAMPLE_RATE_PROD`, `LOG_FORMAT`.
   Все выводы выше сделаны по **умолчаниям из кода** и там, где это важно,
   помечены как умолчание.
2. **Фактическое содержимое таблиц на пилоте.** Сколько строк в
   `events_event` / `analytics_analyticsevent` за сутки, какие `event_name`
   встречаются, есть ли строки в `observability_aidailymetricsummary`,
   непусты ли `eventbus_ingestdlq` и PEL Redis. См. §8.
3. **Состав `CELERY_BEAT_SCHEDULE` в живом beat-процессе.** Замерялся исходник
   `config/settings/base.py`; окружение может добавлять записи (механизма такого
   добавления в коде не нашёл, но не исключаю).
4. **`ayla-ai-core` и `ayla-knowledge` не открывались** — предмет замера в них
   не заходит; если события испускаются оттуда, я этого не увижу.
5. **Тесты не запускались.** Режим read-only; ни одного `pytest` не выполнено,
   ни одно число «N passed» в отчёте не приводится и не подразумевается.
6. **Не проверено, доходят ли алерты `page()` в Telegram фактически** — только
   что код их формирует и что два из трёх вызывающих не запланированы.
7. **Не измерено, покрывает ли `apps/orchestrator/pipeline.py` telegram-канал**
   так же, как MAX: точка захвата replay в конвейере общая, но фактический
   маршрут telegram-хода через конвейер построчно не прослежен.
8. **Не проверено, есть ли внешний BI (Metabase/Superset), подключённый к базе
   каталога вне репозитория.** В коде подключения нет; за пределами кода оно
   может существовать.

---

## 8. Прошу снять на контуре (у меня нет ssh)

Каждая команда — одной строкой, для `api-dev.gobeauty.site`.

1. Объём аналитической шины бота за сутки и состав имён:
   `psql -c "SELECT event_name, count(*) FROM events_event WHERE created_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;"`
2. Сколько строк ушло мимо канонического словаря (счётчик к F-30.8):
   `journalctl -u ai-bot-platform --since '24 hours ago' | grep -c 'events.emit.non_canonical'`
3. Есть ли вообще в базе имена, которых я не нашёл в коде:
   `psql -c "SELECT event_name, count(*) FROM events_event WHERE event_name IN ('conversation_started','message_received','intent_classified','pipeline_error','safety.outbound_blocked') GROUP BY 1;"`
4. Аналитика каталога за сутки:
   `psql -h <ayla-db> -c "SELECT event_name, count(*) FROM analytics_analyticsevent WHERE created_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;"`
5. Пишется ли `recommendation_shown` хоть кем-нибудь (проверка F-30.7):
   `psql -h <ayla-db> -c "SELECT count(*), max(created_at) FROM analytics_analyticsevent WHERE event_name = 'recommendation_shown';"`
6. Заполняется ли дашборд AI-качества (проверка F-31.5 живьём):
   `psql -c "SELECT count(*), max(snapshot_date) FROM observability_aidailymetricsummary;"`
7. Работает ли корреляция «ход→бронь»:
   `psql -c "SELECT count(*) FILTER (WHERE success_correlated_at IS NOT NULL) AS correlated, count(*) AS total FROM observability_airequestmetric WHERE created_at > now() - interval '7 days';"`
8. Живой состав расписания beat (единственная прямая проверка F-31.5):
   `cd /opt/ai-bot-platform && ./venv/bin/python -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.production');django.setup();from django.conf import settings;print(sorted(settings.CELERY_BEAT_SCHEDULE))"`
9. Живые значения знаковых переменных:
   `grep -E '^(EVENT_FANOUTS|OTEL_EXPORTER_OTLP_ENDPOINT|OTEL_TRACES_SAMPLE_RATE|SENTRY_DSN|REPLAY_SAMPLE_RATE_PROD|LOG_FORMAT)=' /etc/ai-bot-platform/.env`
10. Копятся ли необработанные ходы в PEL (проверка F-31.7):
    `redis-cli --scan --pattern 'ingress:*' | xargs -I{} redis-cli XPENDING {} default`
11. Непуста ли DLQ входящей шины:
    `psql -c "SELECT count(*), max(dead_lettered_at) FROM eventbus_ingestdlq;"`
12. Пишутся ли replay-следы (проверка F-31.4 живьём):
    `psql -c "SELECT count(*), min(captured_at), max(captured_at) FROM replay_replaytrace WHERE captured_at > now() - interval '24 hours';"`
13. Уходят ли вообще OTel-трассы:
    `journalctl -u ai-bot-platform --since '24 hours ago' | grep -c 'otel.configure_skipped'`

---

## 9. Точные команды воспроизведения

```
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git rev-parse origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git rev-parse origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "recommendation_id" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "recommendation_id" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "decision_id" origin/dev -- '*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "resolve_recommendation" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/miniapp_api/pending_intent.py
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/events/vocabulary.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git show origin/dev:analytics/event_catalogue.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "RECOMMENDATION_SHOWN\|recommendation_shown" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "\"conversation_started\"\|CONVERSATION_STARTED" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "emit(" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "EVENT_FANOUTS" origin/dev -- config/
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "Event.objects\|from apps.events.models import" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -in "X-Trace\|X-Request-Id\|X-Correlation\|traceparent" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "headers" origin/dev -- apps/integrations/ayla/
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "request_id" origin/dev -- users/middleware.py
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:config/settings/base.py | sed -n '1198,1560p' | grep '^    "'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -rn "aggregate_ai_metrics_daily" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -rn "monitor_post_flip_violations" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "page(" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/workers/consumer.py | sed -n '140,190p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:config/settings/base.py | sed -n '2270,2330p'
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git show origin/dev:djangoProject/settings/base.py | sed -n '1095,1200p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "REPLAY_SAMPLE_RATE\|REPLAY_RETENTION_DAYS" origin/dev -- config/settings/
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "recorder_capture" origin/dev -- 'apps/**/*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -rn "prometheus\|Counter(\|Histogram(" origin/dev -- '*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -rln "CELERY_BEAT_SCHEDULE" origin/dev -- 'tests/' 'apps/**/tests/'
```

---

## 10. Убрано за собой

**Ничего не создавалось.** Ни временных файлов, ни веток, ни worktree, ни
контейнеров, ни записей в БД, ни коммитов. Единственный созданный артефакт —
этот файл отчёта. Рабочие деревья обоих репозиториев не трогались, чекаут
`ai-bot-platform` остался на `feat/recommendation-boundary-client`, как и был.
