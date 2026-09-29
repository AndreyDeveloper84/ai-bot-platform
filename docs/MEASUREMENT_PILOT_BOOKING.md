# Замер: сквозной путь брони (матрица готовности пилота, пп. 20, 21, 23, 24)

**Режим:** MEASURE-FIRST, read-only. Ничего не чинилось, не коммитилось, PR не открывался.
**Дата замера:** 09.09.2026. **Исполнитель:** сабагент окна `ayla-06`.
**Свод:** `docs/BRIEF_PILOT_READINESS_COMMON.md`.

---

## 1. Базы замера

| repo | канон-ветка | фактический SHA | дата коммита | чем снято |
|---|---|---|---|---|
| `djangoproject-catalog` | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | 2026-09-09 06:50:27 +0300 | `git show origin/dev:<путь>`, `git grep origin/dev` |
| `ai-bot-platform` | `origin/dev` | `b1a119bdfb26765bc75ce35dfbf1dd82227183d0` | 2026-09-09 19:14:11 +0300 | `git show origin/dev:<путь>`, `git grep origin/dev` |
| `ayla-knowledge` | `origin/main` | `207eeb638580e529aaff66e0d1412aa6b559c0a3` | — | `git grep origin/main` (только для сверки канона AYLA-DEC-0021/0022) |

SHA совпали с указанными в общем своде — сверено `git rev-parse origin/dev` в обоих репозиториях.

**Чекаут `ai-bot-platform` стоит на чужой ветке `feat/recommendation-boundary-client`.** Рабочее дерево
не читалось ни разу: все цитаты бот-стороны — из `git show origin/dev:`. Чекаут каталога — `dev`, но
читалось всё равно через `git show origin/dev:`.

**Не замерялось живьём.** Ни одна строка ниже не является утверждением о боевом контуре
`api-dev.gobeauty.site`: ssh есть только у заказавшего окна. Живые значения флагов — в разделе 9.

---

## 2. Executive verdict — по строке на ребро и на вопрос

### Рёбра пути

| # | Ребро | Класс | Одной строкой |
|---|---|---|---|
| E1 | рекомендация → intent | **MISSING** | `RecommendationDecision` отдаёт только `CandidateRef(kind, id)`; ни `recommendation_id`, ни lineage в бронь не едет — у `Appointment` нет ни одного поля происхождения |
| E2 | PendingBookingIntent (п.20) | **CONTRADICTS_CANON** | канон требует backend-owned, TTL 30 мин, opaque ref; существует клиент-авторский черновик в Redis бота, TTL 10 мин, с содержательными полями |
| E3 | deep link → Mini App | **EXISTS** (канон соблюдён «по другой причине») | в ссылке едет только слаг маршрута или `reschedule_<uuid>`; содержательные параметры физически запрещены валидатором MAX |
| E4 | выбор offer/мастера/даты/времени | **EXISTS** | общий резолвер `resolve_bookable_service` на чтении и на записи — витрина и запись не могут разойтись в том, что бронируется |
| E5 | свежая проверка доступности | **PARTIAL** | время перепроверяется транзакционно (advisory lock + conflict check); цена, длительность, активность мастера/услуги читаются **до** транзакции; цена ни с чем не сверяется |
| E6 | создание брони (п.21) | **PARTIAL** | одна точка истины `CreateBookingService`, 5 входов в каталоге + 3 писателя в боте; safety-гейт на записи отсутствует полностью |
| E7 | идемпотентность | **PARTIAL** | у create — уникальный индекс + advisory lock, но **умолчание `uuid4()` тихо отключает ключ** на двух клиентских входах; у cancel/reschedule — полноценная машинерия и обязательный заголовок |
| E8 | «Мои записи» | **EXISTS** | `GET /api/v1/internal/me/bookings/` живёт, отдаёт `derived_status`; **не отдаёт `version` и affordance отмены/переноса** |
| E9 | отмена (п.23) | **PARTIAL** | политика 24ч/2ч/50% исполняется, гонка двойной отмены закрыта `select_for_update` + машиной состояний; денежная сторона политики на пути отмены тестом не подтверждена |
| E10 | перенос (п.24) | **CONTRADICTS_CANON** | 4ч исполняются; лимита 3 переносов, lineage (`reschedule_of`/`root_appointment_id`/`reschedule_count`), replacement-семантики и Slot Hold нет ни строкой; **из Mini App на Ayla-пути перенос отдаёт 409** |

### Ответы на обязательные вопросы

1. **PendingBookingIntent (п.20).** Backend-owned сущности в `djangoproject-catalog` **не существует** — `git grep -i "PendingBooking\|BookingIntent"` по `origin/dev` пуст. Существуют две разные бот-сторонние сущности: `PendingBookingAction` (Postgres, TTL **10 мин**, CAS-погашение — гейт разрушительного действия) и `pending_booking_intent` (Redis, TTL **600 с**, клиент-авторский черновик Mini App). Канонические TTL 30 мин, backend-owned и opaque ref не выполнены ни одной из них. **В deep link едет непрозрачное:** слаг маршрута или `reschedule_<UUID>`; содержательные параметры невозможны — валидатор MAX запрещает `?`, `=`, `&`. Контекст брони переносится не ссылкой, а `sessionStorage` клиента + Redis-кэшем на `/auth/verify`.
2. **Booking (п.21).** Истиной брони владеет каталог: `Appointment` + `CreateBookingService` — **единственный** писатель, все 5 входов каталога идут через него. Проверки одинаковы по построению, различаются **только по `actor_role`** (клиент получает полный контракт, персонал теряет notice/grid/frame/closure, override снимает всё кроме конфликта). Идемпотентность держится на `Appointment.idempotency_key unique=True` + `pg_advisory_xact_lock(specialist_id)` + `SELECT … FOR UPDATE`. **Двойное нажатие не создаёт дубль** — но не благодаря ключу, а благодаря conflict-check: без заголовка второе нажатие получает `409 SLOT_NOT_AVAILABLE`, то есть свою же бронь ему называют чужой занятостью.
3. **Свежая перепроверка.** Транзакционно перепроверяются: пересечение интервала, тайм-офф, рамка расписания, закрытие салона, сетка (для клиента), TUR-отзыв, внешняя занятость (за флагом, умолчание OFF). **Из клиента не берётся ничего** — цена и длительность резолвятся сервером. Но именно поэтому **silent substitution возможна**: `CreateBookingDTO` не имеет поля ожидаемой цены/длительности, `create_appointment` не кладёт цену в тело, `price_quoted` в intent записывается и **никогда ни с чем не сравнивается**. Изменилась цена между показом и записью — клиент получает 201 и другую сумму без единого слова.
4. **Отмена и перенос.** Политики исполняются в каталоге (`policies.py:122-124`, `:269`), на бот-стороне политик нет вовсе. **Лимита 3 переносов не существует** — ни в каталоге, ни в боте; `reschedule_count` в боте инкрементируется и не читается. Права: отмена/перенос owner-scoped (`filter(client=request.user)`), cross-tenant закрыт и покрыт. Гонка двойной отмены закрыта, гонка переноса закрыта advisory lock; **но lost-update на безверсионном мобильном переносе — действующая, зафиксированная зелёным тестом политика.**
5. **Safety на пути брони — ПОДТВЕРЖДАЮ P0, и он шире, чем был заявлен.** `git grep "requires_health_check" origin/dev -- appointments/` в каталоге **пуст**; `SafetyState` в каталоге живёт только в `recommendation/`. `ai/tools_handlers.py:245` и `ai/application/services/action_service.py:105` флаг не читают. Гейт бота читают ровно две строки разговорного канала. **Точек создания брони, читающих флаг: 0 из 8.** Кандидат в STOP-блокеры — да, и претендент на первое место.

---

## 3. Сводка по классам

| CURRENT STATE | число |
|---|---|
| `EXISTS` | 5 |
| `PARTIAL` | 7 |
| `MISSING` | 4 |
| `CONTRADICTS_CANON` | 5 |
| `DEAD_CODE` | 1 |
| `STALE_SPEC` | 1 |
| `UNKNOWN_NOT_MEASURED` | 4 |
| **всего находок** | **27** |

| PILOT IMPACT | число |
|---|---|
| `STOP` | 3 (F-01, F-02, F-16) |
| `DEGRADED` | 12 |
| `POST_PILOT` | 12 |

| TEST STATUS по предмету | число сценариев |
|---|---|
| `E2E_GREEN` | 0 |
| `CROSS_BOUNDARY` | 0 |
| `CONTRACT_ONLY` | 4 |
| `UNIT_ONLY` | 6 |
| `MISSING` | 5 |

---

## 4. Находки

### 4.1 Safety на пути брони

#### F-01 · Ни одна точка создания брони не читает `requires_health_check` · `MISSING` · **STOP**

`djangoproject-catalog@95c917e6`:

```
$ git grep -n "health_check" origin/dev -- appointments ai tenants payments
(пусто)
```

`resolved_requires_health_check` читается ровно в трёх местах, ни одно не на записи:
`services/serializers.py:261` (отдать боту), `users/recommendation_source.py:206`, `recommendation/_stages.py:364`.

Все восемь точек создания брони и их отношение к флагу:

| # | repo | точка | читает флаг |
|---|---|---|---|
| 1 | catalog | `appointments/views.py:196` — `POST /api/v1/appointments/` (мобильный клиент, JWT) | **нет** |
| 2 | catalog | `appointments/views.py:279` — `POST /api/v1/appointments/walk-in/` | **нет** |
| 3 | catalog | `appointments/internal_api.py:199` — `POST /api/v1/internal/appointments/` (бот Bearer) | **нет** |
| 4 | catalog | `tenants/appointments_api.py:491` — салонная консоль | **нет** |
| 5 | catalog | `ai/application/services/action_service.py:62,167` — AI-консьерж, смонтирован `djangoProject/urls.py:150` | **нет** |
| 6 | bot | `apps/skills/booking/skill.py` → `provider.py:217` — разговорный канал | **ДА** (`skill.py:1037`, `:1893`) |
| 7 | bot | `apps/miniapp_api/views.py:879` / `:1134` — Mini App | **нет** |
| 8 | bot | `apps/admin_api/views_booking_create.py` | **нет** |

Гейт (`ai-bot-platform@b1a119bd:apps/skills/booking/skill.py:1477-1496`) — fail-closed и хорошо сделан:

```python
    if _booking_via_ayla():
        resolved = _resolved_health_check_for_edge(tenant, master_id, service_id)
        if resolved is not None:
            ...
            return bool(resolved)
        # Edge not mirrored → fail closed. See #1034. Nothing may open it:
        return True
```

Но его собственный докстринг (`skill.py:1469-1475`) описывает границу дословно:

> Note what this gate is and is not. No other booking entry point in this
> codebase consults it — ``apps/booking/services/create.py``,
> ``apps/admin_api/views_booking_create.py`` and the miniapp all create
> bookings without reading the flag — and Ayla's ``appointments`` app does
> not enforce it server-side either. It is the conversational channel's
> routing policy ("hand this one to a human"), not a system-wide safety
> interlock.

При `BOOKING_VIA_AYLA_REST=OFF` тот же гейт читает `CatalogService.requires_health_check` по int
`external_id`, и **промах даёт `False`** (`skill.py:1498-1514`) — то есть в OFF-режиме гейт
fail-open.

#### F-02 · Канон 09.09.2026 требует гейта именно на исполнении, и требует не fail-open · `CONTRADICTS_CANON` · **STOP**

`Ayla_Safety_Architecture_v1_FINAL_FREEZE_2026-09-09.md:241-248`:

> ## M5 --- requires_health_check
> `requires_health_check=true` означает mandatory safety evaluation перед
> соответствующей recommendation/**execution**. …
> ```
> requires_health_check=true + no valid SafetyResult
> → UNKNOWN / fail-closed
> ```

Тот же документ, `:503-505` — владелец истины назван поимённо:

> ## Backend Ayla
> Owns authoritative Capability/CanonicalService/TenantOffer facts,
> `requires_health_check`, … active/service/provider/**booking**/availability/transaction truth

Тот же документ, чек-лист `:736`:

> -   [ ] requires_health_check cannot fail open.

Обе стороны дословно противоречат друг другу: канон говорит «execution», «backend Ayla владеет
booking truth», «cannot fail open»; код каталога на записи флаг не читает вовсе, то есть fail-open
по определению. Соблюдение канона выполняется ровно на одной из восьми поверхностей, и та —
не backend.

#### F-03 · Fail-closed safety работает, но только на витрине рекомендаций · `EXISTS` · `POST_PILOT`

`recommendation/_stages.py:241-253`: заявленный `NOT_APPLICABLE` опровергается содержанием —
кандидат с `requires_health_check` переводит выдачу в fail-closed как при `UNKNOWN`. Дисциплина
образцовая. Она гейтит **совет**, а не **действие**: путь «клиент сам выбрал услугу в Mini App»
рекомендательный конвейер не проходит вовсе.

#### F-04 · `contraindications` не участвует в решении · `PARTIAL` · `DEGRADED`

Поле есть в модели каталога (`services/models.py:114` соседняя пара) и синкается, но в боте только
логируется (`skill.py:1053`, `:1904`) — на решение не влияет.

---

### 4.2 Пункт 20 — PendingBookingIntent

#### F-05 · Backend-owned intent отсутствует · `MISSING` · `DEGRADED`

```
$ cd djangoproject-catalog && git grep -rni "PendingBooking\|BookingIntent\|booking_intent" origin/dev -- '*.py'
(пусто)
```
Единственный `deep_link` в каталоге — `notifications/models.py:60`, другой предмет.

#### F-06 · Существующие сущности — две, обе бот-сторонние, ни одна не соответствует канону · `CONTRADICTS_CANON` · `DEGRADED`

| свойство | канон владельца | `PendingBookingAction` | `pending_booking_intent` |
|---|---|---|---|
| владелец | backend (каталог) | бот, Postgres | бот, Redis; **содержимое пишет клиент** |
| TTL | 30 мин | 10 мин (`apps/bookings/pending_actions.py:74`) | 600 с (`apps/miniapp_api/pending_intent.py:60`) |
| что несёт | opaque ref | `payload` JSON + CAS-погашение | `master_id, service_id, slot_iso, price_quoted, note, loyalty_apply, entry_point` |
| назначение | cross-surface handoff | гейт разрушительного действия в чате | черновик через OAuth-редирект |

`apps/bookings/pending_actions.py:73-74` дословно:
```python
# 10-minute TTL — module-scope constant so tests can monkeypatch.
PENDING_ACTION_TTL = timedelta(minutes=10)
```
Ни env, ни settings — переопределяется только аргументом, который в проде не передаётся.

CAS сделан правильно, `pending_actions.py:297-301`:
```python
    rowcount = PendingBookingAction.all_tenants.filter(
        pk=token,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)
```
`expires_at__gt=now` внутри фильтра — TOCTOU между python-проверкой и UPDATE закрыт.

Intent пишется клиентом: `apps/miniapp_api/views.py:320-327` принимает `pending_booking_intent`
из тела запроса Mini App, санирует по белому списку и кладёт в кэш. Сервер тут — хранилище,
не автор.

#### F-07 · Уборки просроченных `PendingBookingAction` нет · `MISSING` · `POST_PILOT`

Докстринг `pending_actions.py:46-52` обещает «Phase 2 can add a daily cleanup beat». Задачи в
celery-конфиге нет. Строки копятся без ограничения — предмет для эксплуатации, не для пилота.

#### F-08 · Deep link непрозрачен, и это защищено на уровне валидатора · `EXISTS` · —

`apps/orchestrator/visits.py:516-526`:
```python
    if web_app:
        return {
            "label": label,
            "callback": f"{RESCHEDULE_PAYLOAD_PREFIX}{booking_id}",
            "web_app": web_app,
        }
    if miniapp_url:
        return {
            "label": label,
            "url": f"{miniapp_url.rstrip('/')}/{reschedule_route(booking_id)}",
        }
```
`apps/channels/max/outbound.py:696`:
```python
OPEN_APP_PAYLOAD_RE = re.compile(rf"[A-Za-z0-9_-]{{0,{OPEN_APP_PAYLOAD_MAX_CHARS}}}")
```
`?`, `=`, `&`, `:` запрещены; `fullmatch` на `:751`, отказ 400. `git grep "t\.me/"` в репозитории
пуст — площадка MAX, не Telegram. Содержательные параметры в deep link **невозможны**.

Оговорка по правилу «не доверять именам»: `cb:book:pick_master:<sp>:<svc>`
(`apps/orchestrator/visits.py:586-589`) несёт содержательные id — но это inline-callback внутри
чата, не deep link в Mini App. Разные предметы.

#### F-09 · Рекомендация не связана с бронью ничем · `MISSING` · `POST_PILOT`

`recommendation/_types.py:502-517` — `RecommendationDecision` не сохраняется резолвером
(«Резолвером НЕ сохраняется»), выдаёт `RankedCandidate.candidate_ref = CandidateRef(kind, id)`.
`git grep "recommendation_id\|decision_id\|intent_id" -- appointments/models.py` пуст.
Атрибуции «эта бронь пришла из этой рекомендации» не существует.

---

### 4.3 Пункт 21 — Booking

#### F-10 · Одна точка истины записи · `EXISTS` · —

`CreateBookingService._execute_atomic` — единственный писатель `Appointment`. Все пять входов
каталога (F-01, строки 1-5) вызывают его. Хорошая архитектура; вся остальная критика ниже — про
то, что в него не приходит и что он не проверяет.

#### F-11 · Транзакционная защита времени — сделана правильно · `EXISTS` · —

`appointments/infrastructure/db_locks.py:20-41` — `pg_advisory_xact_lock(hashtextextended(specialist_id))`
первым оператором атомарного блока, с прямым объяснением, почему `select_for_update` на пустом
конфликтном наборе не серилизует ничего. Далее `create_booking_service.py:288-299` — conflict check
под `select_for_update`.

Оговорка из того же файла (`db_locks.py:23-28`): **«No-op on non-Postgres backends (SQLite unit
tests)»**. CI обоих репо поднимает Postgres 16, так что защита в принципе проверяема — см. раздел 6,
проверяется она ровно одним тестом и не на create.

#### F-12 · Silent substitution цены возможна и не имеет ни одного барьера · `MISSING` · `DEGRADED`

`appointments/application/dto.py:22-66` — `CreateBookingDTO` не имеет ни `expected_price`, ни
`expected_duration`, ни версии оффера.
`ai-bot-platform:apps/integrations/ayla/booking_client.py:1009-1015` — тело create:
```python
        body = {
            "client_id": client_id,
            "specialist_id": specialist_id,
            "service_id": service_id,
            "start_datetime": start_datetime,
            "payment_required": payment_required,
        }
```
Цены нет. Снимок цены ставится сервером из свежего резолва
(`create_booking_service.py:400-419` → `BookingSnapshot.create`).

При этом `price_quoted` **записывается** в intent (`apps/miniapp_api/pending_intent.py:80`), и
`git grep -iE "expected_price|price_mismatch|PRICE_CHANGED|quoted_price"` по обоим репо пуст:
поле принимается, валидируется, хранится 10 минут и не сверяется ни с чем. Это фикция записи —
величина, которая выглядит как договорённость о цене и не связывает ничего.

**Наблюдаемое поведение:** салон меняет цену между показом карточки и нажатием «подтверждаю» —
клиент получает `201` и другую сумму, ни один код ошибки не возникает.

То же и с длительностью: `resolved.duration_minutes` берётся свежим, конфликт считается по новому
интервалу, клиенту про удлинение визита не сообщается.

#### F-13 · Проверки активности мастера и услуги — вне транзакции · `PARTIAL` · `POST_PILOT`

`create_booking_service.py:118-190` (`_validate_pre_transaction`) — `is_booking_enabled`,
`ProfileStatus.ACTIVE`, `resolve_bookable_service`, окно брони — всё **до** `@transaction.atomic`.
Окно TOCTOU узкое (мастер деактивируется ровно между валидацией и коммитом) и практически
незначимо, но названо здесь, потому что «перепроверяется транзакционно» верно только для времени.

#### F-14 · Внешняя занятость перепроверяется на create и **не** перепроверяется на reschedule · `PARTIAL` · `POST_PILOT`

`create_booking_service.py:369-380` — recheck-at-confirm за флагом.
`appointments/application/services/_booking_guards.py` — функции `check_grid_alignment`,
`_check_time_off`, `check_schedule_frame`, `check_tenant_closure`, `apply_common_booking_guards`;
`EXTERNAL_BUSY` среди них нет, `apply_common_booking_guards` его не зовёт.
Умолчание флага: `djangoProject/settings/base.py:862` — `EXTERNAL_BUSY_ENABLED = … "false"`.
Живое значение не замерено.

---

### 4.4 Идемпотентность

#### F-15 · Машинерия create: уникальный индекс есть, но умолчание входа его отключает · `PARTIAL` · `DEGRADED`

`appointments/models.py:103-104`:
```python
    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
```
`create_booking_service.py:265-273` — lookup по ключу под `select_for_update` внутри advisory lock.

Умолчание на двух клиентских входах:
`appointments/views.py:191-193` и `:276-278`:
```python
        idempotency_key = request.META.get(
            'HTTP_X_IDEMPOTENCY_KEY', str(uuid4()),
        )
```
`appointments/internal_api.py:78-96` (`_idempotency_key_from`) — то же, и **докстринг признаёт это дословно**
(`internal_api.py:78-94`):

> This fallback feeds ``CreateBookingDTO.idempotency_key`` — a per-call value that, when
> server-generated, guarantees nothing across retries (a fresh UUID each time means
> CreateBookingService still runs unconditionally once per HTTP call). It exists purely so create
> keeps working for a header-less legacy caller; **it is NOT a dedup mechanism.**

#### F-16 · Тот же дефект уже исправлен в третьем входе — контрактом отказа, а не удалением умолчания · `CONTRADICTS_CANON` · **STOP**

`tenants/appointments_api.py:439-473` (DRF-1232) — салонная консоль:
```python
        key = (request.META.get("HTTP_X_IDEMPOTENCY_KEY") or "").strip()
        if not key:
            return error_response(
                "IDEMPOTENCY_KEY_REQUIRED",
                "X-Idempotency-Key header is required. Reuse the same value "
                "when retrying a booking, or a retry will create a second "
                "appointment.",
                status_code=400,
            )
```
и там же, дословно, диагноз ровно того умолчания, которое живёт на двух других входах:

> Inventing a value per request kept that machinery running while guaranteeing
> it could never match — every retry arrived with a key nothing had ever been stored under, so the
> caller got a duplicate booking and a 201 that looked like success. … create
> is the only one of the three with key-based de-duplication, and the
> only one where a missing key silently destroys it.

**Почему это STOP, а не DEGRADED.** Само по себе двойное нажатие дубля не создаёт: второй запрос
натыкается на conflict-check и получает `409 SLOT_NOT_AVAILABLE`. Опасен другой, штатный сценарий:
клиент подтверждает бронь → ответ теряется (таймаут, обрыв сети) → ретрай приходит с **новым**
сгенерированным ключом → conflict-check отвечает «слот занят» → клиент читает это как «не
записалось», выбирает **другое** время и записывается ещё раз. **Две брони, клиент знает про одну,
мастер видит две, слот выкинут.** Именно тот момент, ради которого идемпотентность и существует, —
и именно на нём умолчание её снимает.

Формально бот от этого защищён: он всегда шлёт детерминированный ключ (F-17). Незащищены мобильный
клиент (`views.py:192`) и любой сторонний вызывающий internal API. Класс `CONTRADICTS_CANON`
поставлен потому, что канон де-факто зафиксирован в самом репозитории — комментарием DRF-1232, —
и два входа ему противоречат.

#### F-17 · Ключи бота детерминированы, но правило реализовано трижды · `PARTIAL` · `POST_PILOT`

`ai-bot-platform:apps/skills/booking/provider.py:550-561`:
```python
def _idempotency_key(external_user_id: str, op: str, *parts: Any) -> str:
    """Deterministic idempotency key so a retried bot turn can't double-write."""
    seed = "|".join([external_user_id, op, *(str(p) for p in parts)])
    key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    ...
    if not key:
        raise ValueError(f"empty idempotency key for op={op!r} — refusing to write")
    return key
```
Дубль того же правила инлайном во вьюхе Mini App — `apps/miniapp_api/views.py:961-971`
(тот же seed, тот же sha256[:32]) и `:1575-1576` для отмены.
Третье место — `apps/admin_api/views_booking_create.py:140-141` — генерирует `uuid.uuid4()`,
то есть идемпотентности не даёт.
Передаётся заголовком: `apps/integrations/ayla/booking_client.py:729-730`.

#### F-18 · Ключ AI-консьержа каталога не различает мастера и услугу · `PARTIAL` · `DEGRADED`

`ai/application/services/action_service.py:288-291`:
```python
    def _idempotency_key(conversation_id: UUID, slot_dt: datetime) -> str:
        return f"ai-{conversation_id}-{slot_dt.isoformat()}"[:100]
```
Seed — только разговор и время. Две брони в одном разговоре на одно время к **разным** мастерам
или на **разные** услуги дадут один ключ: вторая молча вернёт первую как «уже созданную».
Сценарий редкий, но отказ тихий, а не громкий.

#### F-19 · Cancel/reschedule защищены на порядок лучше create · `EXISTS` · —

`appointments/infrastructure/idempotency.py` — таблица `IdempotencyKey`, лукап по
`(user, operation_name, key, target_id)`, `IdempotencyConflict` (422 на другое тело),
`IdempotencyInFlight` (409), TTL, Stripe-семантика «записывать ответ на всех путях, включая
ошибочные». Внутренние ручки требуют заголовок **обязательно**
(`internal_api.py:270-274`, `:404-409` — `IDEMPOTENCY_KEY_REQUIRED`, 400 до всякой мутации).
Асимметрия между create и cancel/reschedule — самая крупная неровность контракта записи.

---

### 4.5 «Мои записи»

#### F-20 · Ручка есть, affordance нет · `PARTIAL` · `DEGRADED`

`appointments/records_urls.py` — `GET /api/v1/internal/me/bookings/`, `…/<id>/`,
`…/<id>/repeat-intent/`. `records_status.py` — таксономия `derived_status` из 17 значений,
сделана всерьёз.

`records_api.py:103-127` — в ответе **нет** `version`, `can_cancel`, `can_reschedule`,
`refund_percent`. Клиент не может показать «отмена бесплатна ещё 3 часа», не может знать,
что перенос уже запрещён (осталось меньше 4 ч), и не может передать `expected_version`,
который внутренний reschedule требует обязательно.

Обходной путь существует: `InternalAppointmentReadView` (`internal_api.py:502-540`, DRF-1233)
отдаёт `version` и разрешён владельцу брони. То есть контрактно путь замкнут, но ценой второго
запроса на каждую бронь; докстринг этой ручки прямо описывает, почему она понадобилась
(зеркало бота несло `version` у 2 броней из 23 на замере 21.08.2026 — это чужой замер месячной
давности, здесь он приведён как цитата мотивации, а не как факт «сегодня»).

#### F-21 · На Ayla-пути «мои записи» читаются из зеркала бота, не из каталога · `PARTIAL` · `DEGRADED`

`apps/miniapp_api/views.py:1476` (`_bookings_list_ayla`), `:1531` (`_booking_detail_ayla`) —
читают локальный `RemoteBookingProxy`, а не каталог по сети. Свежесть списка равна свежести
доставки outbox-событий, а не запросу. Расхождение зеркала с истиной — отдельный предмет,
здесь не замерялся.

---

### 4.6 Пункт 23 — Отмена

#### F-22 · Политика исполняется, гонка закрыта · `EXISTS` · —

`appointments/domain/policies.py:122-124`:
```python
    FREE_CANCEL_HOURS = 24
    PARTIAL_REFUND_HOURS = 2
    PARTIAL_REFUND_PERCENT = 50.0
```
`_classify_initiator` (`:148-168`) отказывается обслуживать незнакомого инициатора вместо того,
чтобы молча посчитать его клиентом, — с прямым объяснением, что молчаливое умолчание однажды
выставило клиенту счёт за решение салона.

Двойная отмена: `cancel_reschedule_service.py:189-193` — `select_for_update` + перечитывание
статуса + `BookingStateMachine.transition` внутри блокировки → вторая отмена получает
`InvalidStateTransitionError` (422). Гонка закрыта.

#### F-23 · Политика жёстко зашита, ни тенант, ни владелец её не настраивают · `PARTIAL` · `POST_PILOT`

24/2/50 — константы класса. Решение владельца «настраиваемое расписание» на политику
отмены не распространено. Для пилота, вероятно, достаточно; названо, чтобы не выглядело
настраиваемым.

#### F-24 · `refund_percent` считается до блокировки · `PARTIAL` · `POST_PILOT`

`cancel_reschedule_service.py:127-155` — `can_cancel` и `get_refund_percent` вызываются на
до-блокировочном чтении, в атомарный блок приходит уже число. Практический вред близок к нулю
(порог временной), но «перепроверяется транзакционно» про деньги отмены неверно.

---

### 4.7 Пункт 24 — Перенос

#### F-25 · Из Mini App на Ayla-пути перенос отсутствует, а кнопка в него ведёт · `MISSING` · `DEGRADED`

`ai-bot-platform:apps/miniapp_api/views.py:1355-1370`:
```python
def _reschedule_unavailable_on_ayla_path() -> HttpResponse:
    """409 for the reschedule pair when BOOKING_VIA_AYLA_REST is ON.
    ...
    The seam itself is genuinely absent, not merely
    unrouted: ``_proxy_booking_to_dict`` reports ``reschedulable: False``.
    DRF-1349.
    """
    return _error(
        "invalid_state",
        "reschedule is not available on the Ayla path",
        409,
    )
```
Обе ручки (`/bookings/<id>/reschedule`, `/reschedule/confirm`) отвечают 409. При этом
`apps/orchestrator/visits.py:525` продолжает строить кнопку на `/customer/records/<id>/reschedule`,
а фронт зовёт именно эти ручки (`apps/miniapp/src/lib/api.ts:720-732`).

Живое значение `BOOKING_VIA_AYLA_REST` на пилоте **не замерено** — умолчание `false`
(`config/settings/base.py:771`). Если на контуре ON, эта кнопка — рабочая на вид дверь в 409;
если OFF, работает локальная логика, но тогда весь Ayla-путь брони выключен, и половина
измеренного выше кода на пилоте не исполняется. **Оба ответа меняют картину готовности, поэтому
это первый пункт запроса на контур.**

#### F-26 · Канон AYLA-DEC-0022 не реализован по шести позициям · `CONTRADICTS_CANON` · `POST_PILOT`

`ayla-knowledge@207eeb63:05 Architecture/Ayla Core Domain Model Specification.md`:

| канон | строка | код |
|---|---|---|
| same-ID reschedule только при **неизменных цене и длительности**, иначе replacement | `:1055` | replacement-ветки нет вовсе; `cancel_reschedule_service.py:410-413` сохраняет длительность и не смотрит на цену — same-ID всегда |
| `reschedule_of`, `root_appointment_id`, `reschedule_count` | `:1033-1035`, `:1057` | `git grep` по каталогу — **пусто** |
| лимит 3 успешных self-service переносов на lineage | `:1078` | нет ни в каталоге, ни в боте; `reschedule_count` бота (`apps/booking/services/transitions.py:464-465`) инкрементируется и **не читается ни разу** — `DEAD_CODE` |
| Slot Hold, TTL 15 мин | `:285`, `:949` | `git grep -i "slot_hold\|hold_until"` по каталогу — **пусто** |
| late-window >1 ч → manual proposal | `:1117` | вместо этого жёсткий отказ за 4 ч (`policies.py:269`); механизма proposal нет |
| reschedule proposal / Reservation с TTL | `:1100-1114` | отсутствует |

Оговорка: сам документ несёт статус **«Draft / Proposed — substantively developed, internally
incomplete»** (`:89`), при этом AYLA-DEC-0021/0022 в нём помечены как снятые с блокирующих. Что из
этого входит в пилот — решение владельца; здесь зафиксировано расхождение, а не требование.

#### F-27 · Lost-update на безверсионном мобильном переносе — действующая политика, закреплённая зелёным тестом · `CONTRADICTS_CANON` · `DEGRADED`

`cancel_reschedule_service.py:370-399` — при `expected_version is None` пишется предупреждение
в лог и перенос выполняется:
```python
            logger.warning(
                "reschedule.unversioned_command booking_id=%s basis=%s "
                "current_version=%s — expected_version omitted, ",
```
Мобильный сериализатор оставляет поле опциональным (`appointments/serializers.py:214`), внутренний
(бот) — обязательным (`:187`). Тест
`appointments/tests/test_reschedule_wave1_hardening.py::test_default_gate_open_two_unversioned_requests_lost_update`
**фиксирует потерю обновления как ожидаемое поведение**. Зелёный прогон здесь означает «дефект на
месте и согласован», а не «дефекта нет».

---

### 4.8 Свежесть слотов

#### F-28 · Слоты кэшируются дважды по 60 с, инвалидация асинхронная · `PARTIAL` · `POST_PILOT`

Каталог: `appointments/infrastructure/cache/slot_cache.py:23` — `SLOTS_CACHE_TTL_SECONDS = 60`.
Бот: `apps/integrations/ayla/booking_client.py:74` — `SLOT_CACHE_TTL_S = 60`.
Инвалидация каталога идёт через outbox-воркер (`appointments/infrastructure/outbox_worker.py:78-135`),
цикл 10 с (`djangoProject/settings/base.py:956-965`).

Худший случай устаревания показанного слота ≈ 60 + 60 + 10 с. Двойной брони это не даёт —
транзакционный conflict-check ловит, — но клиент видит занятое время как свободное и получает
`409` после нажатия. Класс `PARTIAL`, потому что «свежая проверка» существует на записи и
отсутствует на показе.

Оговорка к правилу «не доверять докстрингам»: `slot_cache.py:5` утверждает «For MVP uses
LocMemCache» — это **устаревший комментарий** (`STALE_SPEC`): `djangoProject/settings/base.py:1073-1084`
задаёт `django_redis.cache.RedisCache`. Межпроцессная инвалидация работает.

---

## 5. Подтверждённые противоречия — обе стороны дословно

**П-1. Гейт здоровья: канон против кода.**

Канон, `Ayla_Safety_Architecture_v1_FINAL_FREEZE_2026-09-09.md:736`:
> `-   [ ] requires_health_check cannot fail open.`

Код, `ai-bot-platform@b1a119bd:apps/skills/booking/skill.py:1469-1475`:
> «No other booking entry point in this codebase consults it … and Ayla's ``appointments`` app does
> not enforce it server-side either. It is the conversational channel's routing policy … **not a
> system-wide safety interlock.**»

**П-2. Идемпотентность create: репозиторий против самого себя.**

`djangoproject-catalog@95c917e6:tenants/appointments_api.py:449-455`:
> «Inventing a value per request kept that machinery running while guaranteeing it could never match
> — every retry arrived with a key nothing had ever been stored under, so the caller got a duplicate
> booking and a 201 that looked like success.»

`djangoproject-catalog@95c917e6:appointments/internal_api.py:96` и `appointments/views.py:192`, `:277`:
```python
        idempotency_key = request.META.get('HTTP_X_IDEMPOTENCY_KEY', str(uuid4()))
```

**П-3. Перенос из Mini App: кнопка против ручки.**

`ai-bot-platform:apps/orchestrator/visits.py:522-526` строит ссылку на экран переноса.
`ai-bot-platform:apps/miniapp_api/views.py:1360-1363`:
> «The seam itself is genuinely absent, not merely unrouted: ``_proxy_booking_to_dict`` reports
> ``reschedulable: False``.»

**П-4. Лимит переносов: канон против кода.**

`ayla-knowledge:…Core Domain Model Specification.md:1078`:
> «Лимит переносов — policy, не инвариант: MVP default 3 успешных self-service reschedule на lineage»

`ai-bot-platform:apps/booking/services/transitions.py:464-465` — единственное упоминание счётчика,
запись без единого чтения:
```python
        meta["reschedule_count"] = (
            int((row.attribution_metadata or {}).get("reschedule_count", 0)) + 1
```
В `djangoproject-catalog` символа нет вовсе.

---

## 6. Реальность тестов

### Команды отбора (правило §3a общего свода — набор получен грепом, не взглядом)

```bash
# каталог
git grep -l -E "CreateBookingService|CreateBookingDTO|cancel_reschedule_service|CancelReschedule|IdempotencyKey|slot_builder|availability|requires_health_check|action_service|tools_handlers" origin/dev -- '*/tests/*' '*test_*.py' '*conftest.py'
# бот
git grep -l -E "PendingBookingAction|pending_actions" origin/dev -- '*test*'
git grep -n  -E "requires_health_check"                origin/dev -- '*test*'
git grep -l -iE "idempotenc"                           origin/dev -- '*test*'
# конкурентность
git grep -n -E "transaction=True|TransactionTestCase|threading|ThreadPool|concurrent\.futures" origin/dev -- '*test*'
# отсутствия
git grep -n -iE "expected_price|price_mismatch|PRICE_CHANGED|quoted_price"    origin/dev   # пусто в обоих
git grep -n -iE "reschedule_count|MAX_RESCHEDULE|reschedule_limit"            origin/dev   # пусто в каталоге
git grep -n "requires_health_check" origin/dev -- 'appointments/'                          # пусто
git grep -n "validation_race" origin/dev -- '*test*'                                       # пусто
```

Прогон тестов не выполнялся (режим read-only). Ни одно число ниже не является результатом прогона.

### Что покрыто и каким уровнем

| Файл / группа | Уровень | Что реально проверяет |
|---|---|---|
| catalog `appointments/tests/test_services.py` (511 стр., 0 моков) | `UNIT_ONLY` (реальный PG) | create happy/conflict/inactive/too-soon, cancel, reschedule, `TestExternalBusyRecheckAtConfirm` |
| catalog `appointments/tests/test_domain.py` | `UNIT_ONLY` (без БД) | 24ч/2-24ч/50%/<2ч; 4ч на перенос — **чистая политика, без применения** |
| catalog `appointments/tests/test_idempotency_512.py` | `UNIT_ONLY` | идемпотентность **cancel/reschedule**, не create; `IN_FLIGHT` смоделирован ручной вставкой строки, не гонкой |
| catalog `appointments/tests/test_reschedule_wave1_hardening.py` | `UNIT_ONLY` + **одна честная гонка** | пост-локовая перепроверка, версия, тайм-офф; `TestConcurrentReschedule::test_two_concurrent_reschedules_to_same_open_slot_serialise` — 2 потока, `transaction=True`, PG |
| catalog `appointments/tests/test_internal_booking_rest_1016.py` | `CONTRACT_ONLY` | внутренние ручки бота: дедуп ретрая, обязательность ключа на cancel/reschedule |
| catalog `test_tenant_boundary_booking_510.py`, `test_cross_tenant_create_404.py` | `UNIT_ONLY` | чужая бронь, подмена `X-Tenant`, отозванный TUR |
| catalog `ai/tests/test_action_service.py`, `test_tools_handlers.py` | `UNIT_ONLY` (`MagicMock` вместо сервиса брони) | что DTO собран и ключ имеет префикс; сам сервис не исполняется |
| catalog `services/tests/test_catalog_contract_s3d.py`, `test_internal_catalog_api_s3a.py` | `CONTRACT_ONLY` | `requires_health_check` как **поле выдачи**, не как гейт записи |
| catalog `tests/test_api_dev.sh` | реальный HTTP против `dev.gobeauty.site`, **вне CI, запуск руками** | §22: create → идемпотентный повтор → 409 → cancel → 422 → complete |
| bot `apps/skills/booking/tests/test_skill.py` (~3200 стр.) | `UNIT_ONLY` (`FakeYClients`) | перепроверка слота при тапе; ~30 кейсов health-гейта, включая fail-closed на незеркалированном ребре |
| bot `apps/bookings/tests/test_booking_callbacks.py` | `UNIT_ONLY` | `test_double_tap_idempotent`, `test_expired_returns_polite_reply`, `test_consume_pending_cas_filter_excludes_expired`, cross-tenant токен |
| bot `apps/miniapp_api/tests/test_create_booking_idempotency.py` | `UNIT_ONLY`, **фикстурная согласованность** | апстрим — класс `_DedupingAylaClient` в том же файле; повторы **последовательные**; докстринг сам признаёт отсутствие честного красного прогона |
| bot `apps/integrations/ayla/tests/*`, `tests/smoke/test_ayla_booking_roundtrip.py` | `CONTRACT_ONLY` (`httpx.MockTransport`, «no DB, no skill stack») | заголовки, тела, маппинг 4xx/5xx |
| bot `tests/e2e/test_ayla_integration.py` | реальный HTTP, **skip без токенов** | nutrition/profile/recommendations — **брони нет** |

### Чего теста НЕТ — поимённо

| Сценарий | Вердикт | Чем подтверждено |
|---|---|---|
| **а) двойное нажатие / два параллельных создания с одним ключом** | **НЕТ** | последовательные повторы есть (`test_services.py::test_idempotency_same_key_returns_same_booking`, `test_internal_booking_rest_1016.py::test_idempotency_key_dedupes_retry`, bot `test_create_booking_idempotency.py` на самописном стабе). Файлов с `threading` в каталоге три, ни один не бьёт `CreateBookingService` двумя потоками. **Advisory lock + unique index + `select_for_update` на create конкурентным тестом не покрыты.** |
| **б) протухший слот (занят между показом и подтверждением)** | **ЧАСТИЧНО** | статический сетап есть (`test_services.py::test_slot_conflict`; bot `test_slot_taken_offers_fresh_alternatives`); **гонки на create нет** |
| **в) изменившаяся цена** | **НЕТ, и механизма нет** | `git grep -iE "expected_price\|price_mismatch\|PRICE_CHANGED\|quoted_price"` пуст в обоих репо. Проверять нечего — см. F-12 |
| **г) недоступный мастер (стал неактивен / ушёл в time-off после показа)** | **НЕТ** | статические есть (`test_specialist_not_active`, `test_time_off_block_rejected` — только reschedule). Константа `REFUSAL_VALIDATION_RACE` (bot `skill.py:2699`) объявлена как исход именно этой гонки, и `git grep validation_race -- '*test*'` пуст |
| **д) истёкший intent / pending action** | **ЕСТЬ** | `test_booking_callbacks.py::TestExpired::test_expired_returns_polite_reply`, `test_pending_text_gate.py::test_expired_pending_confirm_gets_expired_reply`, `test_dead_ends_drf1492.py::test_expired_cancel_preview_does_not_offer_to_book`, `test_confirm_gate.py::test_consume_expired_returns_expired` |
| **е) `requires_health_check` на создании брони в КАТАЛОГЕ** | **НЕТ, потому что нечего покрывать** | `git grep "requires_health_check" origin/dev -- 'appointments/'` пуст. Это пробел в коде, а не в тестах |
| **ж) отмена 24ч free / 2ч 50%** | **ЕСТЬ, только политика** | `test_domain.py::test_free_cancel_24h_ahead`, `::test_partial_refund_2_to_24h`, `::test_no_refund_under_2h`. Теста «отмена за 3 часа → в платеже реально 50%» нет |
| **з) перенос min 4 ч / лимит 3** | **4ч ЕСТЬ, лимит НЕТ** | `test_domain.py::test_cannot_reschedule_too_soon`. Лимита нет в коде → нет и теста |
| **и) гонка на отмене/переносе** | **ЧАСТИЧНО** | перенос: `test_reschedule_wave1_hardening.py::TestConcurrentReschedule::test_two_concurrent_reschedules_to_same_open_slot_serialise` — единственная честная гонка на всём пути. **Двойной параллельной отмены — нет ни одного теста.** «Отмена одновременно с переносом» — только симуляция без потоков (`TestPostLockRecheck::test_terminal_recheck_rejects_row_cancelled_between_precheck_and_lock`) |
| **к) cross-tenant** | **ЕСТЬ, плотно** | catalog `test_tenant_boundary_booking_510.py` (5 кейсов), `test_cross_tenant_create_404.py`; bot `test_views_create_booking_tenant_guard.py`, `test_booking_transitions_owner_guard.py`, `test_booking_callbacks.py::test_cross_tenant_token_rejected_with_forbidden` |

### Конкурентность и e2e — прямым ответом

- **Конкурентного теста на создание брони одним ключом идемпотентности нет ни в одном из двух
  репозиториев. Конкурентного теста на отмену нет ни в одном. Конкурентный тест на перенос — ровно
  один.** `TransactionTestCase` напрямую — 0 в обоих репо. `transaction=True` без потоков (~35
  файлов) гонку поймать не может по построению.
- **Автоматического `CROSS_BOUNDARY` или `E2E_GREEN` на пути брони бот↔каталог не существует.**
  Ближайшее — ручной bash-скрипт каталога против дев-контура (без бота) и ручные инъекции турнов.
- `e2e-artifacts/`: функциональные следы по брони — `wave1-validation-20260805/`,
  `wave1-rb1-20260805/`, `wave1-t02-phase-a-20260806/`, то есть **04–06.08.2026**. Свежее там только
  `latency-window-20260903/` и `ci-speed-20260903/`, к пути брони отношения не имеющие. Месячные
  следы как факт «сегодня» не цитируются.
- `bot tests/fixtures/contracts/README.md` называет себя единственным источником истины, «обе
  стороны грузят эти байты»; `git grep "fixtures/contracts"` в каталоге **пуст** — каталог пинит
  конверт из своего же `build_envelope`. Общность контракта односторонняя: это проверка
  согласованности фикстуры, а не системы.

---

## 7. Что НЕ замерено — честный список

1. **Живое значение `BOOKING_VIA_AYLA_REST` на пилотном контуре.** Умолчание `false`. От него
   зависит, исполняется ли на пилоте вообще половина измеренного (Ayla-путь), работает ли перенос
   из Mini App и какой из двух health-гейтов (fail-closed или fail-open) активен. **Главный
   незамеренный факт этого отчёта.**
2. Живое значение `EXTERNAL_BUSY_ENABLED` (умолчание `false`).
3. Живые значения `BOOKING_MIN_AHEAD_MINUTES` / `MAX_AHEAD_DAYS` / `SLOT_GRID_MINUTES` — в коде
   60 / 60 / 30, переопределение через settings не проверено на контуре.
4. Сколько на пилотном каталоге услуг с `resolved_requires_health_check=true` и сколько рёбер
   `SpecialistService` не зеркалировано в боте (от этого зависит, гейтит ли гейт хоть что-то).
5. Расхождение зеркала `RemoteBookingProxy` с истиной каталога — «мои записи» на Ayla-пути читаются
   из зеркала, свежесть не замерена.
6. Тесты не запускались — ни один прогон, ни одно число `N passed`.
7. `ayla-ai-core` не открывался: по предмету пути брони пересечений не найдено грепом, но это
   `UNKNOWN_NOT_MEASURED`, а не «нет».
8. `STRICT_TENANT_SCOPE` из общего свода в `djangoproject-catalog@95c917e6` отсутствует
   (`git grep` пуст) — тенантная изоляция на пути брони держится на явных `filter(client=…)` /
   `filter(tenant=…)`, не на менеджере. Живой режим на контуре не проверен.

---

## 8. Точные команды воспроизведения

```
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git rev-parse origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git rev-parse origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "health_check" origin/dev -- appointments ai tenants payments
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "resolved_requires_health_check" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "CreateBookingService()\|create_booking_service_class()" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git show origin/dev:appointments/application/services/create_booking_service.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git show origin/dev:appointments/application/dto.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git show origin/dev:appointments/domain/policies.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git show origin/dev:appointments/infrastructure/db_locks.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "idempotency_key" origin/dev -- appointments/models.py appointments/views.py appointments/internal_api.py tenants/appointments_api.py
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -n "root_appointment_id\|reschedule_of\|reschedule_count\|reschedule_proposal" origin/dev
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -rni "slot_hold\|hold_until" origin/dev -- '*.py'
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git grep -rni "PendingBooking\|BookingIntent\|booking_intent" origin/dev -- '*.py'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/skills/booking/skill.py | sed -n '1460,1520p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/bookings/pending_actions.py | sed -n '40,100p;280,340p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/miniapp_api/pending_intent.py | sed -n '40,115p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/miniapp_api/views.py | sed -n '1350,1372p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:config/settings/base.py | sed -n '765,775p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git show origin/dev:apps/integrations/ayla/booking_client.py | sed -n '996,1035p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "requires_health_check" origin/dev -- apps/miniapp_api apps/admin_api apps/booking/services/create.py
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git grep -n "reschedule_count\|MAX_RESCHEDULE\|reschedule_limit" origin/dev
```

---

## 9. Прошу снять на контуре (ssh есть только у заказавшего окна)

Каждая команда — на боевом контуре `api-dev.gobeauty.site`. Правило свежести: ответ действителен
на дату снятия, не позже.

**Р-1 (высший приоритет). Живое значение флага маршрута брони в боте.**
```
docker exec -i <bot_container> python -c "from django.conf import settings; print('BOOKING_VIA_AYLA_REST =', settings.BOOKING_VIA_AYLA_REST)"
```
Без этого числа половина отчёта — про код, который, возможно, не исполняется.

**Р-2. Живые значения флагов и порогов каталога.**
```
docker exec -i <catalog_container> python manage.py shell -c "from django.conf import settings; print({k: getattr(settings, k, '<MISSING>') for k in ['EXTERNAL_BUSY_ENABLED','BOOKING_MIN_AHEAD_MINUTES','BOOKING_MAX_AHEAD_DAYS','BOOKING_SLOT_GRID_MINUTES']})"
```

**Р-3. Есть ли на пилоте хоть одна услуга, которую гейт здоровья должен останавливать.**
```
docker exec -i <catalog_container> python manage.py shell -c "from services.models import SpecialistService; rows=list(SpecialistService.objects.filter(is_active=True)); print('active_links=',len(rows),'gated=',sum(1 for r in rows if r.resolved_requires_health_check()))"
```

**Р-4. Сколько рёбер не зеркалировано в боте (fail-closed сработает на них, а не на настоящих).**
```
docker exec -i <bot_container> python manage.py shell -c "from apps.catalog.models import MasterService; qs=MasterService.objects.all(); print('total=',qs.count(),'null_flag=',qs.filter(resolved_requires_health_check__isnull=True).count(),'gated=',qs.filter(resolved_requires_health_check=True).count())"
```

**Р-5. Проверка F-16 живьём: одинаковый повтор create БЕЗ заголовка идемпотентности.**
```
curl -sS -X POST "https://api-dev.gobeauty.site/api/v1/internal/appointments/" -H "Authorization: Bearer $AYLA_INTERNAL_API_TOKEN" -H "X-External-User-ID: $TEST_EXTERNAL_USER" -H "Content-Type: application/json" -d '{"client_id":"'$TEST_CLIENT_UUID'","specialist_id":"'$TEST_SPEC_UUID'","service_id":"'$TEST_SVC_UUID'","start_datetime":"'$TEST_SLOT_ISO'","payment_required":false}' -w '\nHTTP %{http_code}\n'
```
Отправить дважды подряд. Ожидание по коду: первый `201`, второй `409 SLOT_NOT_AVAILABLE` (то есть
дубля нет, но клиенту про его собственную бронь говорят «занято»). Снять оба тела ответа целиком.

**Р-6. Проверка F-01 живьём: бронь на услугу с `resolved_requires_health_check=true` мимо чата.**
Взять UUID услуги из Р-3 и повторить Р-5 с ним. Ожидание: `201` без единого упоминания
health-check. Это и есть STOP-блокер в наблюдаемом виде.

**Р-7. Проверка F-25 живьём: перенос из Mini App.**
```
curl -sS -X POST "https://<miniapp_host>/api/miniapp/bookings/$TEST_BOOKING_UUID/reschedule" -H "X-Max-Init-Data: $TEST_INIT_DATA" -H "Content-Type: application/json" -d '{"visit_at":"'$NEW_SLOT_ISO'"}' -w '\nHTTP %{http_code}\n'
```
Ожидание при `BOOKING_VIA_AYLA_REST=ON`: `409 invalid_state`.

**Р-8. Накопление непогашенных pending-строк (F-07).**
```
docker exec -i <bot_container> python manage.py shell -c "from django.utils import timezone; from apps.booking.models import PendingBookingAction as P; print('total=',P.all_tenants.count(),'expired_unconsumed=',P.all_tenants.filter(consumed_at__isnull=True, expires_at__lt=timezone.now()).count())"
```

---

## 10. Убрано за собой

**Ничего не создавал.** Ни файлов (кроме этого отчёта), ни веток, ни worktree, ни контейнеров.
Рабочие деревья обоих репозиториев не изменялись: все чтения — `git show <ref>:<путь>` и
`git grep <шаблон> <ref>`, ни одной операции записи, `checkout`, `stash` или `fetch`. Тесты не
запускались, миграции не применялись. Убирать нечего.
