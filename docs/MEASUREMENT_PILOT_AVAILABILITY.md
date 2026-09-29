# Замер: доступность и «мир изменился между показом и действием»

**Предмет:** пункты 22 (AVAILABILITY), 26 (PROVIDER UNAVAILABLE), 27 (OFFER
UNAVAILABLE) матрицы готовности Controlled Pilot.
**Режим:** MEASURE-FIRST, read-only. Ничего не чинилось, ничего не коммитилось.
**Дата замера:** 09.09.2026. **Исполнитель:** сабагент окна `ayla-06`.

Соседний сабагент отдельно разбирает сам путь брони и идемпотентность
(`docs/MEASUREMENT_PILOT_BOOKING.md`) — здесь они не дублируются.
Подтверждение расписания на `CatalogMaster` — **предмет соседнего живого окна
(PR #1502)**; всё, что ниже, классифицировано по состоянию `origin/dev` на
09.09.2026 и в его правки не углубляется.

---

## 1. Базы замера

| repo | ветка | фактический SHA 09.09.2026 | сверено | чем снято |
|---|---|---|---|---|
| `ai-bot-platform` | `origin/dev` | `b1a119bdfb26765bc75ce35dfbf1dd82227183d0` | совпал с общим сводом | `git rev-parse origin/dev` |
| `djangoproject-catalog` | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | совпал с общим сводом | `git rev-parse origin/dev` |

Рабочий чекаут `ai-bot-platform` стоит на чужой ветке
(`feat/recommendation-boundary-client`), `djangoproject-catalog` — на `dev`.
**Весь замер снят через `git show origin/dev:<путь>` и `git grep <шаблон>
origin/dev`**, рабочие деревья не читались ни разу.

`ayla-ai-core` и `ayla-knowledge` по этому предмету не читались — ни один путь
доступности и ни один отказ через них не проходит.

---

## 2. Executive verdict — по строке на вопрос

**П.22 — источник истины о доступности**

1. Свободные слоты вычисляет **Ayla**: `djangoproject-catalog:appointments/infrastructure/availability/slot_builder.py:29 SlotBuilderService.build` поверх `appointments/application/services/availability_query_service.py:146 _compute_day_availability`; входная ручка для бота — `users/specialists_api.py:244 compute_specialist_day_slots`.
2. Слот складывается из: рамка дня (`SpecialistScheduleException` за дату → иначе `SpecialistWorkingHours` по дню недели) МИНУС дыры (активные записи, `SpecialistTimeOff`, `TenantClosure`, опционально `ExternalBusyInterval`), сетка 30 мин, минимальный горизонт вперёд 60 мин, обеденный перерыв, буфер `buffer_after_minutes` после услуги (обязан поместиться до закрытия).
3. Максимального горизонта **на читающем пути Ayla нет вообще** — `BOOKING_MAX_AHEAD_DAYS=60` живёт только в записи; потолок клиентскому пикеру ставит потребитель (`SlotConfig.max_advance_days` бота).
4. При ПУСТОМ расписании мастера — **«нет слотов»**, не «слоты по умолчанию»: `_get_working_hours` возвращает `None` → `DayAvailabilityDTO(is_working_day=False)` → пустой список. Умолчаний нигде не подставляется.
5. Доступность кэшируется **двумя слоями по 60 с**: Ayla `SLOTS_CACHE_TTL_SECONDS=60` (Redis) и бот `SLOT_CACHE_TTL_S=60`. Совокупная витрина устаревания слота — **до 120 с**, в чате плюс до 10 мин TTL превью.
6. Ответы бота и Ayla по одному мастеру расходиться не могут **на клиентском пикере**: при `BOOKING_VIA_AYLA_REST=True` бот не считает слоты сам, а пересказывает ответ Ayla дословно. Удерживает это одна ветка `apps/miniapp_api/views.py:593`, не тест паритета.
7. **Fallback на локальное зеркало при недоступности Ayla ОТСУТСТВУЕТ, и это сделано намеренно и честно**: пикер отдаёт 503, кабинет мастера отказывается рисовать день. Единственный «фолбэк» — сам флаг `BOOKING_VIA_AYLA_REST` (умолчание `false` → локальный расчёт), то есть переключатель развёртывания, а не рантайм-подмена.

**П.26 — мастер стал недоступен между показом и действием**

8. **Читающий и пишущий пути Ayla спрашивают РАЗНЫЕ столбцы, и расхождение двустороннее.** Чтение: `status=active AND is_available AND user.is_active`. Запись: `is_booking_enabled AND status=active`. `is_booking_enabled` на чтении не спрашивается **никогда** — мастер, поставивший запись на паузу, продолжает продавать слоты и получает отказ только после подтверждения. `is_available` и `user.is_active` на записи не спрашиваются **никогда** — снятый с витрины мастер бронируется прямым POST.
9. Мастер, **исчезнувший из фида Ayla** (удалён, деактивирован, снят с продажи), из зеркала бота НЕ снимается: `upsert_specialists` — upsert-only, реконсиляции мастеров нет ни одной. Строка живёт в каталоге бота с `is_active=True` бессрочно.
10. Что видит человек в Mini App: 404 от Ayla ловится широким `except Exception` и показывается как **«booking system is temporarily unavailable», 503**. Имя состояния обвиняет источник — «система сломалась» вместо «мастер больше не работает».
11. Что видит человек в чате: **любой** отказ Ayla (слот занят, мастер не активен, услуга не активна) схлопывается в одну строку «Не удалось создать запись — переключаю на менеджера» с хендоффом.
12. **Молчаливой подстановки другого мастера нет ни на одном пути.** Реассайн существует только как явное операторское действие с preview, планом на каждую запись и DM клиенту, и он ОТКАЗЫВАЕТСЯ выполняться, когда зеркало Ayla показывает больше живых будущих записей, чем каскад умеет разобрать.
13. Отгул на выбранную дату — единственный случай, обнаруживаемый **на обоих путях одним и тем же кодом** (провайдер занятости на чтении, `SpecialistTimeOff` под локом на записи).
14. Уход из салона (`TenantUserRelationship` роли STAFF отозван) даёт каскад отмены будущих записей со 100% возвратом и отдельным шаблоном уведомления. Каскад гасит **уже созданные** записи; продажу новых он не выключает — `SpecialistProfile.tenant`, `is_available` и `is_booking_enabled` он не трогает.

**П.27 — услуга изменилась между показом и действием**

15. Деактивация/расцепление оффера ловится **одним резолвером** на обоих путях (`services/service_resolver.py:59`), поэтому чтение и запись разойтись не могут: чтение → 404, запись → 422 `SERVICE_NOT_ACTIVE`.
16. Удалённая услуга из зеркала бота не снимается (реконсиляции услуг нет), но деактивированная — снимается: фид `salon-services` НЕ фильтрован по `is_active`, поэтому `CatalogService.is_active` перезаписывается каждый синк (каденция 15 мин).
17. **Изменение длительности между показом и бронью слот НЕ сдвигает — оно молча меняет конец записи.** `end_at` считается на сервере из ТЕКУЩЕГО каталога (`create_booking_service.py:74`), клиент длительность не присылает и сравнить не с чем: в чате бот вообще не знает длительность показанного слота (контракт Ayla отдаёт голые ISO-строки, `duration_s=None`), а Mini App в ответе на создание возвращает `duration_min` СВОЕГО зеркала, а не то, что записала Ayla.
18. **Изменение цены применяется молча по той же схеме**: цена снимается сервером из `SpecialistService.price` в момент записи, от клиента не приезжает, с показанной не сравнивается, в ответ на создание не возвращается.
19. Услуга, уехавшая в другой тенант, ловится (резолвер фильтрует `tenant_id` внутри запроса), переписанный шаблон — тоже (каскад длительности specialist → salon → template).

**П.4 — где ревалидация, а где вера клиенту**

20. Полный список полей и вердикт по каждому — раздел 4.5. Кратко: **длительность, цена, конец записи, комиссия — серверные и не принимаются от клиента вовсе**; время перепроверяется полностью; личность клиента перепроверяется; **`payment_required` приезжает от клиента и не перепроверяется ни на одной из двух сторон** — именно оно решает, брать ли деньги.

**Самое опасное одной фразой**

> `apps/miniapp_api/views.py:997` определяет причину отказа регистрозависимым `"slot" in exc.code`, а Ayla отдаёт `SLOT_NOT_AVAILABLE` заглавными — поэтому единственная ветка, построенная чтобы отличить «время заняли, выберите другое» от прочего, не срабатывает никогда, и все отказы «мир изменился» приезжают в Mini App одинаковым `bad_request` / HTTP 400 «booking rejected».

---

## 3. Сводка по классам числом

Всего находок: **28**.

| CURRENT STATE | число | номера |
|---|---|---|
| `EXISTS` | 9 | A1, A2, A4, P7, P9, P12, O1, O5, O6 |
| `PARTIAL` | 15 | A3, A6, A7, A8, A9, P2, P3, P5, P8, P10, P11, O2, O3, O4, O7 |
| `CONTRADICTS_CANON` | 2 | P1, P6 |
| `UNKNOWN_NOT_MEASURED` | 2 | A5, R2-live |
| `STALE_SPEC` | 0 | — |
| `DEAD_CODE` | 0 | — |
| `MISSING` | 0 | — |

| PILOT IMPACT (оценка исполнителя, решает владелец) | число |
|---|---|
| `STOP` | 2 (P1, P6) |
| `DEGRADED` | 18 |
| `POST_PILOT` | 8 |

| TEST STATUS | число |
|---|---|
| `UNIT_ONLY` | 11 |
| `CONTRACT_ONLY` | 4 |
| `MISSING` | 13 |
| `E2E_GREEN` | 0 |
| `CROSS_BOUNDARY` | 0 |

**Ни одна находка этого предмета не имеет замера на стыке двух систем.**
Все зелёные тесты — либо unit внутри одного репозитория, либо контракт с
подменённым клиентом, у которого обе стороны данных построил один автор.

---

## 4. Находки

### 4.1 П.22 — источник истины о доступности

#### A1. Кто вычисляет слоты и из чего они складываются — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

`djangoproject-catalog:appointments/application/services/availability_query_service.py:196`
— единственное место, где разрешается рамка рабочего дня:

```python
    @staticmethod
    def _get_working_hours(specialist, target_date: date) -> dict | None:
        exception = SpecialistScheduleException.objects.filter(
            specialist=specialist, date=target_date,
        ).first()
        if exception is not None:
            if not exception.is_working_day:
                return None
            return _frame(exception.start_time, exception.end_time,
                          exception.break_start, exception.break_end)
        wh = SpecialistWorkingHours.objects.filter(
            specialist=specialist, day_of_week=target_date.weekday(),
            is_working_day=True,
        ).first()
        if not wh:
            return None
```

Дыры вырезаются провайдерами
(`appointments/infrastructure/availability/providers.py:205 make_read_provider`):
`BookingBusyIntervalProvider` (активные записи), `TimeOffBusyIntervalProvider`
(отгулы), `TenantClosureBusyIntervalProvider` (закрытия салона), плюс
`services/availability.py:20 ExternalBusyIntervalProvider` — **только** когда
`EXTERNAL_BUSY_ENABLED` (умолчание `false`, живое значение не замерено).

Сетка и минимальный горизонт —
`appointments/infrastructure/availability/slot_builder.py:20,23`:

```python
SLOT_GRID_MINUTES = int(getattr(settings, 'BOOKING_SLOT_GRID_MINUTES', 30))
MIN_BOOKING_AHEAD_MINUTES = int(getattr(settings, 'BOOKING_MIN_AHEAD_MINUTES', 60))
```

Буфер после услуги входит в шаг проверки, но НЕ в отдаваемый интервал
(`slot_builder.py:52-82`): слот предлагается только если `duration + buffer`
помещается до закрытия, а клиенту показывается `duration`.

Умолчания настроек Ayla — `djangoProject/settings/base.py:420-422`:
`BOOKING_MIN_AHEAD_MINUTES = 60`, `BOOKING_MAX_AHEAD_DAYS = 60`,
`BOOKING_SLOT_GRID_MINUTES = 30`. **Живые значения env на контуре не замерены.**

#### A2. Пустое расписание = «нет слотов», не «слоты по умолчанию» — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

`_get_working_hours` возвращает `None` → `_compute_day_availability` (строка 172)
отдаёт `DayAvailabilityDTO(date=target_date, is_working_day=False)`, а
`compute_specialist_day_slots` превращает это в `{"date": ..., "slots": []}`.
Ни одного `or DEFAULT`, ни одного «10:00-19:00» в читающем пути **нет**
(проверено грепом по `origin/dev` — сеятель заглушки был снят DRF-1062 и не
заменён).

Прямое следствие для пилота: **мастер без строк `SpecialistWorkingHours` в
Ayla не продаёт ни одного слота ни на одной поверхности**, и никакая ошибка
об этом не сообщается — пикер выглядит как «занято». Сколько таких из 31 —
белое пятно, команда замера в разделе 8.

Покрывает `appointments/tests/test_services.py:385
test_no_working_hours_returns_not_working` — unit.

#### A3. Двухслойный кэш 60 + 60 с; инвалидация неполная — `PARTIAL` / `DEGRADED` / `UNIT_ONLY`

Ayla: `appointments/infrastructure/cache/slot_cache.py:22`
`SLOTS_CACHE_TTL_SECONDS = 60`, ключ
`slots:v1:{specialist_id}:{date}:{service_id}`, бэкенд Redis
(`settings/base.py:1073`, `IGNORE_EXCEPTIONS=True` — сбой кэша деградирует,
не падает).

Бот: `apps/integrations/ayla/booking_client.py:74` `SLOT_CACHE_TTL_S = 60`,
ключ `ayla.booking.slots.v1:times:{tenant}:{specialist}:{service}:{date}`.

Инвалидация в Ayla есть на: создание/отмену/перенос записи (outbox
`cache.invalidate_slots`), `SpecialistScheduleException` и `TenantClosure`
(сигналы `appointments/signals.py:33,40`), запись расписания и отгулов
(`users/schedule_api.py:285,330,441,478`, `users/internal_schedule_api.py:181`).

Инвалидации **НЕТ** на: смену `SpecialistProfile.is_available` /
`is_booking_enabled` / `status`, смену `SalonService.duration_minutes` /
`SpecialistService.price` / `is_active`. Верхняя граница расхождения там —
TTL, то есть 60 с в Ayla плюс 60 с в боте.

#### A4. Fallback на локальное зеркало отсутствует, и это помечено честно — `EXISTS` / `POST_PILOT` / `CONTRACT_ONLY`

Три места, где выбор источника делается явно, и ни в одном нет тихой подмены:

* `ai-bot-platform:apps/miniapp_api/views.py:593` — клиентский пикер. Флаг ON →
  только Ayla; сбой апстрима → 503 (`views.py:481`), а не локальный расчёт.
* `ai-bot-platform:apps/master_api/services/schedule_frame.py:1-31` — экран
  мастера. Дословно: *«The local tables are NOT consulted: a stale answer is
  the defect being fixed, so a silent fallback would resurrect it… the frame
  refuses rather than guesses»*.
* `ai-bot-platform:apps/admin_api/services/availability.py:127` — одобрение
  отгула. Флаг OFF → в Ayla не пишем вовсе; флаг ON и Ayla недоступна → отказ
  503 и заявка остаётся pending, а не «одобрено локально».

Плюс `ai-bot-platform:apps/admin_api/views.py:293-296` честно подписывает
локальную строку расписания в ростере админа: *«`apps.scheduling` is NOT what
serves bookable slots on the pilot»*.

**Вывод по вопросу задачи: пути, которым зеркало может стать источником
ответа клиенту при недоступности Ayla, НЕТ. Единственный путь к зеркалу —
`BOOKING_VIA_AYLA_REST=False`, и это решение развёртывания, а не деградация.**

#### A5. Живое значение `BOOKING_VIA_AYLA_REST` — `UNKNOWN_NOT_MEASURED`

`ai-bot-platform:config/settings/base.py:771`:

```python
BOOKING_VIA_AYLA_REST = os.environ.get("BOOKING_VIA_AYLA_REST", "false").lower() == "true"
```

**Умолчание — `false`.** Задача сообщает, что на контуре `True`; это
утверждение владельца, а не мой замер. Весь раздел 4.2–4.4 написан для
ветки ON и помечен соответственно. Команда снятия — раздел 8.

#### A6. Локальный `SlotConfig.max_advance_days` продолжает резать горизонт на Ayla-пути — `PARTIAL` / `DEGRADED` / `MISSING`

`ai-bot-platform:apps/miniapp_api/views.py:580-584` — клампинг стоит **до**
ветвления по флагу:

```python
    advance_cap = today_local + timedelta(days=config.max_advance_days)
    if date_to > advance_cap:
        date_to = advance_cap
```

Умолчание `DEFAULT_MAX_ADVANCE_DAYS = 60` (`apps/scheduling/models.py:116`)
совпадает с `BOOKING_MAX_AHEAD_DAYS = 60` Ayla, поэтому сегодня это невидимо.
Но потолок продажи задан **строкой в БД бота**, которую Ayla не видит: тенант
с `SlotConfig.max_advance_days=30` молча сузит витрину вдвое против того, что
источник истины готов продать. Живые строки `SlotConfig` не замерены.

#### A7. На читающем пути Ayla нет проверки максимального горизонта — `PARTIAL` / `POST_PILOT` / `MISSING`

`users/specialists_api.py:244 compute_specialist_day_slots` принимает любую
дату: валидируется только формат `YYYY-MM-DD`. `BOOKING_MAX_AHEAD_DAYS`
спрашивается исключительно на записи
(`domain/policies.py:296 DefaultBookingWindowPolicy` и
`create_booking_service.py:221 _validate_staff_time_bounds`).

То есть `GET /api/v1/internal/specialists/{id}/slots/?date=2030-01-01` отдаст
слоты, которые `POST` затем откажется принять с `BOOKING_WINDOW_INVALID`.
Продукт от этого спасает только клампинг A6 в боте — то есть чужая
настройка в чужой базе.

#### A8. Две разные арифметики слотов в двух системах — `PARTIAL` / `POST_PILOT` / `UNIT_ONLY`

| параметр | Ayla (продаёт при флаге ON) | зеркало бота `apps.scheduling` |
|---|---|---|
| сетка | 30 мин (`BOOKING_SLOT_GRID_MINUTES`) | 15 мин (`DEFAULT_SLOT_GRANULARITY_MIN`) |
| буфер | `buffer_after_minutes` **услуги**, только ПОСЛЕ | 5 мин **тенанта**, с ОБЕИХ сторон занятости |
| минимальный горизонт | 60 мин | 60 мин (`DEFAULT_LEAD_TIME_MIN`) |
| максимальный горизонт | 60 дней (только на записи) | 60 дней (`SlotConfig`) |

При флаге ON вторая колонка для клиента инертна. Она НЕ инертна для
`BOOKING_VIA_AYLA_REST=False`, для локального пути создания
(`apps/booking/services/create.py:277-296`) и для админских экранов. Две
арифметики живут рядом, совпадения между ними ничто не удерживает — ни тест,
ни общая константа.

#### A9. У зеркала `WorkingHours` бота нет писателя — `PARTIAL` / `DEGRADED` / `UNIT_ONLY`

Дословно из `djangoproject-catalog:users/internal_schedule_api.py:19-24`:

> *«the master's own schedule screen builds its days from the bot's local
> `apps.scheduling` `WorkingHours`, which no longer has a writer that syncs
> from Ayla — the seeder that manufactured the pilot's 10:00-19:00 stub was
> removed by DRF-1062 and nothing replaced it»*

DRF-1126 закрыл это **чтением** (`GET /internal/specialists/{id}/schedule/`,
которое `schedule_frame.py` использует при флаге ON), а не синхронизацией.
Строки `WorkingHours` в зеркале остаются тем, чем их оставила история.
Заявленные 4 из 31 — ровно этот остаток. **Предмет соседнего окна (PR #1502)
в части подтверждения расписания на `CatalogMaster`; здесь только
зафиксировано состояние на сегодня.**

---

### 4.2 П.26 — мастер стал недоступен между показом и действием

#### P1. `is_booking_enabled` спрашивается ТОЛЬКО на записи — `CONTRADICTS_CANON` / `STOP` / `MISSING`

Обе стороны дословно.

Чтение — `djangoproject-catalog:users/specialists_api.py:513-521`:

```python
    def get_queryset(self) -> QuerySet:
        qs = (
            SpecialistProfile.objects
            .filter(
                status=SpecialistProfile.ProfileStatus.ACTIVE,
                is_available=True,
                user__is_active=True,
            )
```

Запись — `djangoproject-catalog:appointments/application/services/create_booking_service.py:130-134`:

```python
        if not specialist.is_booking_enabled:
            raise SpecialistNotActiveError("Specialist is not accepting bookings")

        if specialist.status != SpecialistProfile.ProfileStatus.ACTIVE:
            raise SpecialistNotActiveError("Specialist profile is not active")
```

`is_booking_enabled` в читающем querysete **не упоминается**. Смысл поля по
модели (`users/models.py:213`): *«Master can pause bookings without
deactivating profile»*.

**Что происходит на пилоте:** мастер ставит запись на паузу → остаётся в
каталоге бота → `/internal/specialists/{id}/slots/` отдаёт его настоящие
свободные часы → клиент выбирает время → на подтверждении Ayla отвечает 422
`SPECIALIST_NOT_ACTIVE`. Отказ приходит **после** того, как человек выбрал
время, и приходит в форме, которую ни одна поверхность не умеет объяснить
(см. P5, P6).

Направление ошибки худшее из двух возможных: система **продаёт то, что не
продаст**.

#### P2. `is_available` и `user.is_active` спрашиваются ТОЛЬКО на чтении — `PARTIAL` / `DEGRADED` / `MISSING`

Зеркальная половина P1: `_validate_pre_transaction`
(`create_booking_service.py:118-134`) достаёт профиль напрямую
`SpecialistProfile.objects.get(id=...)`, минуя каталожный queryset, и не
спрашивает ни `is_available`, ни `user.is_active`.

Следствие: мастер, снятый с витрины (`is_available=False`), для читающих
поверхностей исчезает, а прямой `POST /api/v1/internal/appointments/`
по-прежнему создаёт на него запись. Это не гипотетика: у бота есть путь
(чат, P10), который между показом и записью локальную бронируемость **не
перепроверяет вовсе**.

#### P3. Мастер, исчезнувший из фида Ayla, из зеркала бота не снимается — `PARTIAL` / `DEGRADED` / `MISSING`

`ai-bot-platform:apps/catalog/services/upserter.py:163-165`, дословно:

> *«Missing-from-feed rows are kept as-is (same policy as salon-services:
> upsert-only, no proactive deactivation — documented in the S3B PR report).»*

При этом `is_active` зеркала выводится из полей, которые фид уже отфильтровал
(`apps/catalog/services/http_client.py:888-890`):

```python
        is_active=bool(
            str(row.get("status", "")).lower() == "active" and row.get("is_available", True)
        ),
```

Ayla отдаёт по `/internal/specialists/` **только** активных и доступных
(A-разбор P1), поэтому «стал недоступен» приезжает не строкой `is_active=False`,
а **отсутствием строки**, а отсутствие строки политика зеркала игнорирует.
Реконсиляция в каталоге бота есть ровно одна — для рёбер `MasterService`
(`upserter.py:697-739`); для мастеров и для услуг её нет.

Итог: строка мастера живёт в каталоге бота с `is_active=True` **бессрочно**
после того, как Ayla перестала его знать. Витрина, пикер и рекомендации
продолжают его показывать.

#### P4. Смена состояния мастера в боте не доезжает до Ayla — `PARTIAL` / `POST_PILOT` / `UNIT_ONLY`

Обратное направление P3. Деактивация мастера в боте
(`apps/admin_api/services/master_deactivation.py`) меняет `is_active` /
`archived_at` **только** в зеркале; в Ayla ни `is_available`, ни
`is_booking_enabled` не пишутся (грепом по `origin/dev` писателя нет). Клиента
это сегодня не задевает — все клиентские поверхности ходят через пикер бота, —
но состояние «мастер уволен» существует в двух базах в разных значениях, и
прямой POST в Ayla (P2) его не увидит.

#### P5. В чате любой отказ Ayla схлопывается в один хендофф — `PARTIAL` / `DEGRADED` / `UNIT_ONLY`

Цепочка: Ayla 409/422 → `booking_client.py:789 _fail_status` →
`BookingBadRequestError` → `provider.py:429 _translate_errors` →
`YClientsAPIError` → `tools.py:1195 execute_confirm` → `error="yclients_api_error"`
→ `apps/bookings/callbacks.py:810-822`:

```python
        if result.error in {"yclients_unavailable", "yclients_api_error"}:
            return SkillResult(
                reply_text="Не удалось создать запись — переключаю на менеджера.",
                should_handoff=True,
                handoff_reason="booking_yclients_failure",
            )
```

Отличаются от общего котла ровно два кода: `SUBSCRIPTION_PAST_DUE` (C1,
нейтральный текст) и `stale_version` (перенос). **«Время только что заняли»,
«мастер больше не принимает записи» и «услуга снята» — один и тот же ответ и
один и тот же хендофф на живого человека.** Для «время заняли» это худший из
возможных исходов: правильный ответ («выберите другое время») в системе есть,
и он не используется.

#### P6. Слаг отказа Mini App считается регистрозависимо, поэтому не срабатывает — `CONTRADICTS_CANON` / `STOP` / `MISSING`

Обе стороны дословно.

Бот — `ai-bot-platform:apps/miniapp_api/views.py:988-998`:

```python
        if (exc.code or "").lower() == "subscription_past_due":
            ...
        logger.info("miniapp_api.create_booking.ayla_bad_request err=%s", exc)
        slug = "slot_unavailable" if "slot" in (exc.code or "") else "bad_request"
        return _error(slug, "booking rejected", 409 if slug == "slot_unavailable" else 400)
```

Ayla — `djangoproject-catalog:core/errors.py:111` и
`djangoProject/exception_handler.py:169-172`:

```python
    SLOT_NOT_AVAILABLE = "SLOT_NOT_AVAILABLE"
```
```python
    if isinstance(exc, SlotNotAvailableError):
        return _envelope(
            ErrorCode.SLOT_NOT_AVAILABLE.value, str(exc), status_code=409,
        )
```

Код приезжает в `exc.code` дословно
(`booking_client.py:1429 _err_code` читает `error.code` без нормализации).
`"slot" in "SLOT_NOT_AVAILABLE"` → **`False`**.

Что делает эта строка на самом деле: **никогда** не выдаёт
`slot_unavailable`. Все четыре отказа «мир изменился» —
`SLOT_NOT_AVAILABLE` (409), `SPECIALIST_NOT_ACTIVE` (422),
`SERVICE_NOT_ACTIVE` (422), `BOOKING_WINDOW_INVALID` (400) — становятся
`bad_request` / HTTP 400 «booking rejected».

Что это не может быть недосмотром соседней строки: строка выше, в той же
функции, **лоуэркейзит явно** (`(exc.code or "").lower()`). Автор знал форму
провода и в следующей строке её не применил.

Что это стоит: `_STATUS_BY_SLUG` (`views.py:857`) содержит
`"slot_unavailable": 409` — то есть контракт «время заняли» в Mini App
объявлен, поддержан фронтом и **мёртв**. Клиент, у которого слот увели за
120 с кэша, получает 400 «booking rejected».

#### P7. Отгул на выбранную дату — единственный случай с настоящим паритетом — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

Чтение: `providers.py:64 TimeOffBusyIntervalProvider` в составе
`make_read_provider()`. Запись: `create_booking_service.py:335-344` под
advisory-локом, плюс `_booking_guards.py:59 _check_time_off` на переносе.
Оба пути читают одну таблицу `SpecialistTimeOff` с одинаковым условием
пересечения. Административный override (`dto.time_override`) снимает рамку
и закрытия, но **не снимает отгул** — это записано явно
(`create_booking_service.py:322-325`).

Одобрение отгула через Mini App при флаге ON пишется прямо в Ayla
(`apps/admin_api/services/availability.py:106-113
_block_time_in_ayla` → `POST /internal/specialists/{id}/time-off/`), с
отказом 409 при живых записях в периоде и 503 при недоступности Ayla.
Инвалидация кэша слотов делается там же
(`users/internal_schedule_api.py:181`).

#### P8. Уход из салона: каскад отменяет, но не выключает продажу — `PARTIAL` / `DEGRADED` / `UNIT_ONLY`

`djangoproject-catalog:users/services.py:686-692` — каскад срабатывает при
отзыве `TenantUserRelationship` роли `STAFF`:

```python
    if tur.role == TenantUserRelationship.Role.STAFF:
        cascade_specialist_departure(
            specialist_user=target_user, tenant=tenant, actor=actor,
        )
```

Он отменяет все активные будущие записи со 100% возвратом независимо от окна
(`ForceFullRefundCancellationPolicy`) и уводит уведомление на отдельный
шаблон `appointment_cancelled_specialist_departure`.

Чего он НЕ делает: не трогает `SpecialistProfile.tenant`, `is_available`,
`is_booking_enabled` и не выключает продажу новых слотов. То есть после
отмены прошлых записей мастера можно записать заново — если каталог бота
успел или не успел обновиться. Явно вне области (докстринг `users/services.py:749`):
перевод записи за мастером в новый салон и назначение замены.

#### P9. Молчаливой подстановки другого мастера нет ни на одном пути — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

Проверено грепом по всем клиентским путям (`apps/skills/booking`,
`apps/miniapp_api/views.py`, `apps/booking/services`) — ни одного
автовыбора: `master_id` обязателен на всех входах, отсутствие сопоставления
(мастер, услуга) даёт 404, а не подбор.

Единственный перенос записи на другого мастера —
`apps/admin_api/services/master_deactivation.py`: явное четырёхшаговое
операторское действие, где план обязан назвать каждую будущую запись,
цель реассайна проверяется на выполнение услуги под локом, а клиент
получает DM с текстом, чей хэш кладётся в аудит.

Отдельно ценно: этот каскад **отказывается работать вслепую**
(`master_deactivation.py:868-878`):

```python
        if not inventory_complete:
            raise DeactivationError(
                "inventory_incomplete",
                (
                    f"Ayla mirror reports {mirror_count} live future visit(s) for this "
                    f"master but only {len(current_bookings)} can be reassigned or "
                    "cancelled from here. Deactivating now would strand the difference. "
                    "Resolve those visits in Ayla first."
                ),
                status=409,
            )
```

#### P10. В чате между показом и ✅ — до 10 минут без локальной перепроверки — `PARTIAL` / `DEGRADED` / `MISSING`

`apps/bookings/pending_actions.py:74` `PENDING_ACTION_TTL = timedelta(minutes=10)`.

`confirm_booking` (`apps/skills/booking/tools.py:915-926`) проверяет
`master_id` и `service_id` **только против allow-множества текущего хода
диалога** — то есть против списка, который сам был построен из зеркала.
`execute_confirm` (`tools.py:1076`) на нажатие ✅ **не перепроверяет ничего**
локально: ни `CatalogMaster.objects.bookable()`, ни `master_sale_refusal`,
ни существование ребра `MasterService`. Гейт продажи `master_sale_refusal`
вызывается ровно в двух местах, и оба — локальный путь при флаге OFF
(`apps/booking/services/create.py:263`, `apps/booking/services/transitions.py:436`).

Совокупное окно «мир измерен → запись пишется» в чате:
до 120 с устаревания слота (A3) + до 10 мин TTL превью + до 15 мин
устаревания зеркала каталога (каденция `catalog_sync_every_15min`,
`config/settings/base.py:1229`).

#### P11. 404 «мастера больше нет» показывается как «сервис временно недоступен» — `PARTIAL` / `DEGRADED` / `CONTRACT_ONLY`

`ai-bot-platform:apps/miniapp_api/views.py:469-485`:

```python
        except Exception:  # noqa: BLE001
            logger.exception(
                "miniapp.slots.ayla_unavailable master=%s date=%s", master.id, current,
            )
            return None, _error(
                "upstream_unavailable",
                "booking system is temporarily unavailable",
                503,
            )
```

`except Exception` одинаково ловит сетевой сбой, 5xx и **404 «этого
специалиста в каталоге больше нет»** (`internal_catalog_api.py:252-254`
поднимает `Http404`, а `booking_client._fail_status` превращает 4xx в
`BookingBadRequestError`). Пользователь на исчезнувшего мастера получает
предложение «попробуйте позже», которое не станет правдой никогда.

Тест `apps/miniapp_api/tests/test_slots_ayla_source_1062.py:311
test_upstream_outage_is_503_not_500` закрепляет 503 для `RuntimeError`, и
ничто не отличает от него 404.

#### P12. Прямой выбор мастера в Mini App перепроверяется на записи — `EXISTS` / `POST_PILOT` / `CONTRACT_ONLY`

`apps/miniapp_api/views.py:943` внутри `_create_booking_via_ayla`:

```python
        master = CatalogMaster.objects.bookable().get(id=master_id)
```

`bookable()` читает единый предикат `apps.catalog.master_state.AVAILABLE`
(`is_active AND archived_at IS NULL AND invite_status=accepted AND
ayla_user_id IS NOT NULL`), тот же, что отдаёт пикер. Это отличает Mini App
от чата (P10) в лучшую сторону — но перепроверяется **зеркало**, а зеркало
не знает о P1 и P3.

---

### 4.3 П.27 — услуга изменилась между показом и действием

#### O1. Деактивация и расцепление оффера ловятся одним резолвером — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

`djangoproject-catalog:services/service_resolver.py:59 resolve_bookable_service` —
один и тот же вызов на чтении (`users/specialists_api.py:342-352`,
`allow_salon_fallback=True`) и на записи
(`create_booking_service.py:144-156`). Ветка маркетплейса требует
`is_active`, салонная — активный `SalonService` в тенанте **и** активную
связь `SpecialistService`. Отсутствие любого из трёх даёт одну и ту же
ошибку без утечки существования.

Паритет закреплён именованным тестом
`users/tests/test_internal_slots_amd019.py:169
test_slots_and_create_use_the_same_resolver` — единственный тест паритета
чтения и записи, найденный по всему предмету.

#### O2. Удалённая услуга из зеркала не снимается; деактивированная — снимается — `PARTIAL` / `DEGRADED` / `MISSING`

Разница против P3 существенная и в пользу услуг: фиды
`/internal/catalog/salon-services/` и `/internal/catalog/specialist-services/`
(`djangoproject-catalog:services/internal_api.py:44,69`) **не фильтруют
`is_active`** — они отдают и неактивные строки. Поэтому
`upserter._service_fields` (`apps/catalog/services/upserter.py:138`)
перезаписывает `is_active` каждый синк, а `upsert_master_services`
(строка 625) физически удаляет ребро, помеченное `is_active=False`.

Дыра остаётся ровно одна: **физическое удаление** строки в Ayla приводит к
её исчезновению из фида, а исчезновение из фида политика upsert-only
игнорирует так же, как для мастеров.

#### O3. Изменение длительности не сдвигает слот — оно молча меняет конец записи — `PARTIAL` / `DEGRADED` / `MISSING`

Прямой ответ на вопрос задачи: **начало показанного слота не сдвигается
никогда**; молча меняется другое.

`djangoproject-catalog:appointments/application/services/create_booking_service.py:74`:

```python
        end_at = dto.start_at + timedelta(minutes=resolved.duration_minutes)
```

`resolved` — результат резолвера, прочитанный **в момент записи**. Клиент
длительность не присылает (см. 4.5), поэтому сравнить показанное с
записываемым негде — и никто не сравнивает.

Что при этом происходит на трёх поверхностях:

* **Чат.** Бот вообще не знает длительность показанного слота. Контракт Ayla
  отдаёт голые ISO-строки (`compute_specialist_day_slots` →
  `{"slots": ["2026-09-10T10:00:00+03:00", ...]}`), и
  `booking_client.py:553 _slot_from_wire` кладёт `duration_s=None`, откуда
  `tools.py:849 _to_slot_candidate` выводит `duration_minutes = 0`.
  Обнаружить изменение длительности в чате нечем в принципе.
* **Mini App.** Показывает `CatalogService.duration_min` своего зеркала
  (устаревание до 15 мин) и **в ответе на создание возвращает его же**
  (`apps/miniapp_api/views.py:1022 "duration_min": service.duration_min`),
  а не то, что записала Ayla. Если длительность выросла 60 → 90, клиент
  видит «60» на карточке подтверждённой записи.
* **Ayla.** Пишет корректно и снимает `snapshot_duration_minutes` с
  резолвера. Если новая длительность не помещается — сработает конфликт с
  соседней записью (409) или рамка дня (`check_schedule_frame`), то есть
  отказ будет, но по чужой на вид причине.

Названный автором остаточный зазор
(`_booking_guards.py:117-123`): рамка на записи проверяется **без** буфера,
а чтение требует, чтобы буфер поместился до закрытия. Запись, чей хвостовой
буфер вылезает за закрытие, не предлагается ни одним путём, но принимается.

#### O4. Изменение цены применяется молча — `PARTIAL` / `DEGRADED` / `MISSING`

`create_booking_service.py:404-408`:

```python
        if resolved is not None:
            snapshot_name = resolved.name
            snapshot_duration = resolved.duration_minutes
            snapshot_price = resolved.price
            snapshot_buffer = resolved.buffer_after_minutes
```

Цена — `SpecialistService.price` (ребро «мастер + услуга»), прочитанная в
момент записи. Клиент цену не присылает, серверу не с чем сравнивать,
в ответ на создание Mini App цену не возвращает вовсе.

Отдельная известная дельта, зафиксированная DRF-1067
(`apps/skills/booking/provider.py:319-339`): котировка в чате обязана брать
цену **ребра**, потому что зеркало каталога несёт
`SalonService.base_price`, и две величины расходятся, как только салон
задаёт цены по мастерам. Механизм для котировки построен; сравнения
«показали X — записали Y» нет ни там, ни где-либо ещё.

#### O5. Услуга уехала в другой тенант — ловится — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

`services/service_resolver.py:103-106` — фильтр тенанта **внутри** запроса:

```python
    salon = (
        SalonService.objects
        .filter(id=service_id, is_active=True, tenant_id=tenant_id)
        .first()
    )
```

Строка чужого тенанта неотличима от отсутствующей: 404 на чтении, 422
`SERVICE_NOT_ACTIVE` на записи, без утечки существования.

#### O6. Шаблон переписан — каскад длительности определён, старшинство названо — `EXISTS` / `POST_PILOT` / `UNIT_ONLY`

`services/models.py:562 SpecialistService.resolved_duration` — каскад
specialist → salon → template. Резолвер бронирования для салонной ветки
берёт **сначала** `SalonService.duration_minutes` и опускается в каскад
только при `None` (`service_resolver.py:123-127`), то есть при заданной
салонной длительности персональная переопределяющая длительность мастера
игнорируется. Это решение AMD-019, записанное в коде, а не расхождение:
чтение и запись зовут одну функцию и разойтись не могут.

Практическое следствие для зеркала: бот мирроритт `duration_minutes`
салонной услуги (`http_client.py:829`), то есть ровно ту величину, которую
резолвер предпочтёт. Совпадение здесь — свойство данных, а не контракта:
теста, который бы его удерживал, нет.

#### O7. Mini App на записи не проверяет пару (мастер, услуга) — `PARTIAL` / `DEGRADED` / `CONTRACT_ONLY`

Читающий путь проверяет (`apps/miniapp_api/views.py:570`):

```python
    if not MasterService.objects.filter(master_id=master.id, service_id=service.id).exists():
        return _error("not_found", "master does not perform this service", 404)
```

Пишущий путь (`_create_booking_via_ayla`, строки 879-1030) — **нет**. Гейт,
стоящий до ветвления (`views.py:1252-1264`), спрашивает другое: «есть ли у
этой услуги ХОТЬ ОДИН бронируемый исполнитель». Это записано в коде честно
(`views.py:1238-1241`): *«the Ayla path (`_create_booking_via_ayla`) never
consults the mapping at all — it would have happily booked a masterless
service against any bookable specialist»*.

Ловит это Ayla (`resolve_bookable_service` требует активную связь
`SpecialistService`), поэтому дыры в данных нет. Есть дыра в имени отказа:
исчезнувшее ребро приезжает клиенту не как «этот мастер больше не делает
эту услугу», а как `bad_request` 400 через P6.

---

### 4.4 Что видит человек — сводная таблица

Для каждого случая «мир изменился между показом и действием» при
`BOOKING_VIA_AYLA_REST=True`.

| случай | на каком шаге обнаружено | Mini App | чат |
|---|---|---|---|
| мастер удалён в Ayla | на слотах (Ayla 404) | «сервис временно недоступен», 503 (P11) | «Нет свободных дат у мастера» либо хендофф |
| мастер `is_available=False` | на слотах (выпал из queryset) | то же 503 (P11) | то же |
| мастер `is_booking_enabled=False` | **только на записи**, 422 | `bad_request` 400 «booking rejected» (P6) | «переключаю на менеджера» (P5) |
| мастер снят с продажи в БОТЕ | на пикере (зеркало) | мастер не показан | мастер не в ростере |
| мастер архивирован в БОТЕ, но живёт в Ayla | Mini App — на записи; чат — **нигде** | 404 «master not found or not bookable» (P12) | запись создастся (P10 + P2) |
| у мастера отгул на дату | на слотах И на записи (P7) | слот не показан / 400 при гонке | то же |
| мастер ушёл из салона (TUR отозван) | будущие записи отменены каскадом, продажа не выключена (P8) | зависит от синка зеркала | то же |
| слот заняли за 120 с кэша | на записи, 409 | **`bad_request` 400** вместо 409 `slot_unavailable` (P6) | «переключаю на менеджера» (P5) |
| оффер деактивирован | на слотах И на записи (O1) | 404 на слотах / 400 на записи | «Нет свободных…» / хендофф |
| оффер удалён физически | только на записи (O2) | `bad_request` 400 (P6) | хендофф (P5) |
| длительность изменилась | **не обнаруживается** (O3) | запись создаётся с новым концом, показан старый `duration_min` | запись создаётся молча |
| цена изменилась | **не обнаруживается** (O4) | запись создаётся по новой цене, клиенту не сказано | то же |
| услуга уехала в другой тенант | на записи, 422 (O5) | `bad_request` 400 (P6) | хендофф (P5) |
| ребро (мастер, услуга) удалено | Mini App — на слотах, на записи только через Ayla (O7) | 404 / 400 | хендофф |

---

### 4.5 П.4 — что едет от клиента и что перепроверяется

**Вход Mini App** — `POST /api/miniapp/bookings`
(`apps/miniapp_api/views.py:1036 create_booking`), тело JSON.
**Вход чата** — `PendingBookingAction.payload`, собранный LLM-инструментом
`confirm_booking` и потреблённый `execute_confirm` на нажатие ✅.
**Выход в Ayla** — `POST /api/v1/internal/appointments/`
(`djangoproject-catalog:appointments/internal_api.py:118
InternalBookingCreateSerializer`).

| поле | откуда | перепроверяется в боте | перепроверяется в Ayla | вердикт |
|---|---|---|---|---|
| `client_id` | не от клиента: резолвится из подписанной initData / `X-External-User-ID` | да — субъект берётся из сессии, тело игнорируется (`views.py:928-943`) | **да** — 403 `CLIENT_MISMATCH`, если тело называет не того (`internal_api.py:167-177`) | ревалидируется дважды |
| `master_id` / `specialist_id` | от клиента | Mini App — `bookable()` (P12); **чат — нет** (P10) | частично: существование + `is_booking_enabled` + `status`; **НЕ** `is_available`, **НЕ** `user.is_active` (P2) | дыра, обе стороны |
| `service_id` | от клиента | Mini App — `is_active` + `ayla_service_id`; **чат — только allow-множество хода** | **да**, полностью — резолвер с тенантом и связью (O1) | ревалидируется в Ayla |
| пара (мастер, услуга) | производное | Mini App: на слотах да, на записи нет (O7); чат — нет | **да** — активная `SpecialistService` | ревалидируется в Ayla |
| `visit_at` / `start_datetime` | от клиента | Mini App — только парсинг ISO; чат — только парсинг | **да, полностью**: окно (мин. 60 мин + горизонт 60 дней), сетка 30 мин, рамка дня, перерыв, закрытие салона, отгул, конфликт с активной записью под advisory-локом, внешняя занятость | ревалидируется полностью |
| `payment_required` | **от клиента**, `bool(body.get("payment_required", False))` (`views.py:1073`) | **нет** | **нет** — принимается сериализатором дословно, `confirm_immediately = not payment_required` | **не перепроверяется нигде** (R2) |
| `duration_minutes` | **не передаётся** | — | вычисляется сервером из каталога | серверное, но молча меняющееся (O3) |
| `price` | **не передаётся** | — | вычисляется сервером из `SpecialistService.price` | серверное, но молча меняющееся (O4) |
| `end_datetime` | **не передаётся** | — | `start + duration` на сервере | серверное |
| `platform_fee` / комиссия | **не передаётся** | — | `DefaultCommissionPolicy`, плоские 90 ₽ | серверное |
| `client_name`, `client_phone`, `master_name`, `service_name` | от LLM / профиля (только чат) | нет | **в Ayla не отправляются вовсе** | локальный дисплей; в `BookingRequest` пишутся как есть |
| `X-Idempotency-Key` | детерминированный SHA-256 от (внешний id, операция, мастер, услуга, время, payment_required) | — | да — на создании есть серверный фолбэк, на переносе/отмене обязателен | предмет соседнего сабагента |

#### R2. `payment_required` — клиентское поле, решающее, брать ли деньги — `PARTIAL` / `POST_PILOT` / `MISSING`

`apps/miniapp_api/views.py:1073`:

```python
    payment_required = bool(body.get("payment_required", False))
```

и дальше без изменений уезжает в Ayla, где
`InternalBookingCreateSerializer.payment_required` принимает его дословно, а
`create_booking` выводит `confirm_immediately = not payment_required`
(`internal_api.py:190-193`). То есть тело запроса решает, появится ли строка
`Payment` и уйдёт ли бронь сразу в `CONFIRMED`.

На Controlled Pilot это инертно: тенанты пилота в allowlist
`BOOKING_NO_PREPAYMENT_TENANTS` (`apps/skills/booking/tools.py:1239-1280`),
предоплаты нет ни на одном пути. **Живое содержимое allowlist не замерено.**
Вне пилотного профиля поле становится способом получить подтверждённую
запись без оплаты одной строкой в теле.

---

## 5. Подтверждённые противоречия — обе стороны дословно

### C1. Читающий и пишущий путь Ayla спрашивают разные столбцы (P1 + P2)

**Сторона «продаём»** — `djangoproject-catalog:users/specialists_api.py:513-521`:

```python
            .filter(
                status=SpecialistProfile.ProfileStatus.ACTIVE,
                is_available=True,
                user__is_active=True,
            )
```

**Сторона «записываем»** — `djangoproject-catalog:appointments/application/services/create_booking_service.py:122-134`:

```python
            specialist = SpecialistProfile.objects.select_related("user").get(
                id=dto.specialist_id
            )
        except SpecialistProfile.DoesNotExist:
            raise SpecialistNotActiveError(...)

        if not specialist.is_booking_enabled:
            raise SpecialistNotActiveError("Specialist is not accepting bookings")

        if specialist.status != SpecialistProfile.ProfileStatus.ACTIVE:
            raise SpecialistNotActiveError("Specialist profile is not active")
```

Пересечение — один столбец `status`. Два из трёх условий каждой стороны
вторая сторона не знает. Направления ошибок противоположные и оба реальные.

### C2. Код Mini App противоречит собственному намерению строкой ниже собственной нормализации (P6)

**Намерение** — `ai-bot-platform:apps/miniapp_api/views.py:857`:

```python
    "slot_unavailable": 409,
```

**Реализация** — `ai-bot-platform:apps/miniapp_api/views.py:997`:

```python
        slug = "slot_unavailable" if "slot" in (exc.code or "") else "bad_request"
```

**Провод** — `djangoproject-catalog:core/errors.py:111`:

```python
    SLOT_NOT_AVAILABLE = "SLOT_NOT_AVAILABLE"
```

**Доказательство, что автор знал форму провода** — строка 988 той же функции:

```python
        if (exc.code or "").lower() == "subscription_past_due":
```

### C3. Докстринг зеркала обещает мониторинг деактивации, политика зеркала её игнорирует (P3)

**Что пишет зеркало** — `ai-bot-platform:apps/catalog/services/http_client.py:130`:

> *«`is_active` mirrors status==active AND is_available upstream»*

**Что отдаёт источник** — `djangoproject-catalog:users/specialists_api.py:517-519`:
queryset фильтрован по `status=active` и `is_available=True`, то есть строк
с `is_active=False` в фиде **не бывает никогда**.

**Что делает зеркало с исчезнувшей строкой** —
`ai-bot-platform:apps/catalog/services/upserter.py:163`:

> *«Missing-from-feed rows are kept as-is… upsert-only, no proactive
> deactivation»*

Поле, объявленное зеркалом деактивации, деактивацию мониторить не может:
источник сообщает о ней отсутствием, а отсутствие политика отбрасывает.

---

## 6. Реальность тестов

### Что покрыто и каким уровнем

| предмет | тест | уровень |
|---|---|---|
| пустое расписание → нет слотов | `djangoproject-catalog:appointments/tests/test_services.py:385 test_no_working_hours_returns_not_working` | `UNIT_ONLY` |
| рамка дня: шаблон, исключение, закрытие салона — на чтении и на записи | `appointments/tests/test_schedule_frame_holes_1062.py` (классы `TestScheduleException`, `TestTenantClosure`, `TestWritePathHonoursSchedule`, `TestFrameAndHolesCompose`) | `UNIT_ONLY` |
| «нет объявленного расписания» ≠ «закрыто» | `test_schedule_frame_holes_1062.py:313 TestEnforcementStartsWithADeclaredSchedule` | `UNIT_ONLY` |
| клиент против персонала на закрытый день | `test_schedule_frame_holes_1062.py:359 TestGuardAppliesToClientsNotStaff` | `UNIT_ONLY` |
| `is_booking_enabled=False` → отказ на ЗАПИСИ | `appointments/tests/test_services.py:198 test_specialist_not_active` | `UNIT_ONLY` |
| деактивированная / расцепленная услуга → 422 на записи | `appointments/tests/test_amd019_salon_persistence.py:264,275 TestSalonErrorShapes` | `UNIT_ONLY` |
| **чтение и запись зовут ОДИН резолвер услуги** | `users/tests/test_internal_slots_amd019.py:169 test_slots_and_create_use_the_same_resolver` | `UNIT_ONLY` — единственный тест паритета в предмете |
| салонная услуга без связи → 404 на слотах | `users/tests/test_internal_slots_amd019.py:139 test_salon_without_link_404` | `UNIT_ONLY` |
| внешняя занятость блокирует запись при флаге ON | `appointments/tests/test_services.py:473,494` | `UNIT_ONLY` |
| каскад ухода мастера: отмена + 100% возврат + шаблон | `users/tests/test_specialist_departure_cascade_q2.py` | `UNIT_ONLY` |
| Ayla — источник слотов, локальное расписание не решает | `ai-bot-platform:apps/miniapp_api/tests/test_slots_ayla_source_1062.py:180 TestAylaIsTheSourceOfTruth` | `CONTRACT_ONLY` (клиент Ayla подменён) |
| несвязанная услуга → 409, апстрим лёг → 503 | `test_slots_ayla_source_1062.py:288 TestFailureModes` | `CONTRACT_ONLY` |
| гейт продажи мастера под локом (3 причины отказа) | `ai-bot-platform:apps/booking/tests/test_master_sale_gate_drf1548.py` | `UNIT_ONLY` — **только локальный путь, флаг OFF** |
| витрина каталога обещает ровно тех, кого покажет пикер | `apps/miniapp_api/tests/test_views.py TestCatalogShelfMatchesPicker` | `UNIT_ONLY` |
| C1 `subscription_past_due` → нейтральный слаг | `apps/miniapp_api/tests/test_create_booking_ayla.py:274` | `CONTRACT_ONLY` |

### Чего теста НЕТ — поимённо

1. **Нет теста «мастер с `is_booking_enabled=False` не должен продавать слоты».**
   Именно его отсутствие держит P1 живым. Ни один фикстур
   (`users/tests/test_internal_slots_amd019.py:51`,
   `users/tests/test_internal_catalog_1016.py:49`,
   `appointments/tests/test_internal_booking_rest_1016.py:83`) этот флаг не
   переключает — он выставляется в `True` и больше не трогается. Форма
   недостающего теста: поставить `is_booking_enabled=False`, дёрнуть
   `GET /internal/specialists/{id}/slots/`, потребовать пустой список
   или 404. Сегодня он покраснеет.
2. **Нет теста «мастер с `is_available=False` не должен бронироваться прямым
   POST»** — зеркальная половина. Ни один тест в
   `djangoproject-catalog` не выставляет `is_available=False` вообще
   (проверено грепом по всем `*/tests/*`).
3. **Нет теста на маппинг слага отказа на Ayla-пути Mini App.**
   Единственный `assert "slot_unavailable" in resp.json()["error"]`
   (`apps/miniapp_api/tests/test_views.py:977`) снят на ЛОКАЛЬНОМ пути, где
   слаг рождается локальным `BookingCreateError`, а не приезжает от Ayla.
   Форма недостающего теста: подсунуть клиент Ayla, поднимающий
   `BookingBadRequestError(status_code=409, code="SLOT_NOT_AVAILABLE")`,
   и потребовать HTTP 409 + слаг `slot_unavailable`. Сегодня он покраснеет
   и это будет targeted proof для P6.
4. **Нет теста «404 от Ayla — это не 503».** `test_upstream_outage_is_503_not_500`
   закрепляет 503 для `RuntimeError` и ничем не отличает исчезнувшего
   мастера от лежащего сервиса (P11).
5. **Нет теста «мастер, пропавший из фида, снимается с продажи в зеркале»** —
   потому что поведения нет (P3). Есть только противоположный, закрепляющий
   upsert-only.
6. **Нет теста «изменение длительности между показом и бронью»** — ни в одном
   репозитории. Форма: записать слот, поменять `duration_minutes`,
   создать бронь, сравнить `end_datetime` и `snapshot_duration_minutes` с
   показанным. Сегодня он зафиксировал бы молчаливое изменение (O3).
7. **Нет теста «изменение цены между котировкой и бронью»** (O4).
8. **Нет теста на чат-путь «мастер стал небронируемым между превью и ✅»** —
   `execute_confirm` вообще не имеет теста на перепроверку состояния (P10).
9. **Нет теста, различающего исходы отказа в чате.** Все ветки
   `_dispatch_confirm` для `yclients_api_error` дают один текст, и тест,
   который потребовал бы разных текстов для «слот занят» и «мастер снят»,
   отсутствует (P5).
10. **Нет теста на `payment_required` как на неревалидируемое поле** (R2).
11. **Нет ни одного теста на стыке двух систем** по всему предмету: каждый
    «контрактный» тест бота подменяет клиент Ayla объектом, который написал
    тот же автор. По правилу общего свода такой тест проверяет
    согласованность фикстуры, а не системы.
12. **Нет теста паритета `SlotConfig.max_advance_days` бота и
    `BOOKING_MAX_AHEAD_DAYS` Ayla** (A6, A7).
13. **Нет теста, удерживающего равенство «длительность, которую мирроритт
    бот» и «длительность, которую предпочтёт резолвер Ayla»** (O6) —
    сегодня они совпадают по факту данных, а не по контракту.

### Прогонов тестов не делалось

Режим read-only; ни один набор не запускался, ни одно число `N passed`
в этом отчёте не приводится и не подразумевается. Правило отбора проверок
из §3a общего свода применялось бы так (греп приложен для будущего
исполнителя, сам прогон — нет):

```
git grep -l "compute_specialist_day_slots\|AvailabilityQueryService\|SlotBuilderService\|resolve_bookable_service" origin/dev -- 'djangoproject-catalog/**/*.py'
git grep -l "_slots_from_ayla\|get_available_times\|master_sale_refusal\|_create_booking_via_ayla" origin/dev -- 'ai-bot-platform/**/*.py'
```

---

## 7. Что НЕ замерено — честный список

1. **Живое значение `BOOKING_VIA_AYLA_REST` на контуре.** Умолчание `false`.
   Весь раздел 4.2–4.4 написан для ветки ON по утверждению задачи.
2. **Живое значение `EXTERNAL_BUSY_ENABLED`.** Умолчание `false`; при OFF
   внешняя занятость в слоты не входит вообще.
3. **Живые значения `BOOKING_MIN_AHEAD_MINUTES`, `BOOKING_MAX_AHEAD_DAYS`,
   `BOOKING_SLOT_GRID_MINUTES`, `BOOKING_PLATFORM_FEE_RUB`** — приведены
   умолчания из `settings/base.py`, env контура не читался.
4. **Сколько из 31 продаваемого мастера имеют в Ayla хотя бы один
   `is_working_day=True`.** Это главное белое пятно предмета: от него
   зависит, сколько мастеров вообще способны показать клиенту слот.
   Команда — раздел 8, пункт 1.
5. **Сколько мастеров имеют `is_booking_enabled=False` при
   `status=active AND is_available=True`** — то есть население дыры P1 на
   сегодня. Раздел 8, пункт 4.
6. **Сколько строк `CatalogMaster` в зеркале бота с `is_active=True`
   отсутствуют в фиде Ayla** — население дыры P3. Раздел 8, пункт 5.
7. **Строки `SlotConfig` по тенантам пилота** (`max_advance_days`,
   `slot_granularity_min`, `buffer_min`, `lead_time_min`) — A6.
8. **Содержимое `BOOKING_NO_PREPAYMENT_TENANTS`** — R2.
9. **Фактический бэкенд кэша на контуре.** В `settings/base.py:1073`
   объявлен Redis, но `DJANGO_REDIS_IGNORE_EXCEPTIONS=True` — при
   недоступности Redis кэш деградирует молча, и отличить «TTL 60 с» от
   «кэш выключен» можно только на контуре.
10. **Реальная каденция и успешность `catalog_sync_every_15min`** — от неё
    зависит верхняя граница устаревания зеркала в O3/O4/P3.
11. **Ни одного ответа живой ручки.** Все утверждения о поведении выведены
    из кода по `origin/dev`; ни `curl`, ни `manage.py shell` не запускались
    (ssh есть только у заказавшего окна).
12. **Поведение при `BOOKING_VIA_AYLA_REST=False`** разбиралось только в той
    мере, в какой нужно было отличить его от ветки ON. Локальный путь
    создания (`apps/booking/services/create.py`) прочитан, но не замерен
    как предмет.
13. **Изменения PR #1502** (подтверждение расписания на `CatalogMaster`) —
    предмет соседнего живого окна, намеренно не читались.

---

## 8. Прошу снять на контуре

Каждая команда — одной строкой, дословно. Хост Ayla —
`api-dev.gobeauty.site` (`djangoproject-catalog`), хост бота —
`ai-bot-platform`. Все команды read-only.

**1. Главное белое пятно: сколько из продаваемых мастеров вообще способны
показать слот** (агрегат, а не построчная сумма — по правилу «гейту нужен
счётчик»). Хост Ayla:

```
python manage.py shell -c "from users.models import SpecialistProfile; from appointments.models import SpecialistWorkingHours; sold=set(SpecialistProfile.objects.filter(status='active', is_available=True, user__is_active=True).values_list('id', flat=True)); wh=set(SpecialistWorkingHours.objects.filter(specialist_id__in=sold, is_working_day=True).values_list('specialist_id', flat=True)); print('sold=%d with_working_day=%d without=%d' % (len(sold), len(wh), len(sold-wh)))"
```

**2. То же в разрезе тенантов пилота** — чтобы «27 из 31» не оказалось
свойством одного салона. Хост Ayla:

```
python manage.py shell -c "from collections import Counter; from users.models import SpecialistProfile; from appointments.models import SpecialistWorkingHours; rows=list(SpecialistProfile.objects.filter(status='active', is_available=True, user__is_active=True).values_list('id','tenant_id','display_name')); wh=set(SpecialistWorkingHours.objects.filter(specialist_id__in=[r[0] for r in rows], is_working_day=True).values_list('specialist_id', flat=True)); c=Counter((str(t), i in wh) for i,t,_ in rows); [print(k, v) for k, v in sorted(c.items())]"
```

**3. Живые значения флагов и порогов.** Хост Ayla:

```
python manage.py shell -c "from django.conf import settings; print({k: getattr(settings, k, '<absent>') for k in ['BOOKING_MIN_AHEAD_MINUTES','BOOKING_MAX_AHEAD_DAYS','BOOKING_SLOT_GRID_MINUTES','BOOKING_PLATFORM_FEE_RUB','EXTERNAL_BUSY_ENABLED']})"
```

Хост бота:

```
python manage.py shell -c "from django.conf import settings; print({k: getattr(settings, k, '<absent>') for k in ['BOOKING_VIA_AYLA_REST','BOOKING_NO_PREPAYMENT_TENANTS','CATALOG_SYNC_THROTTLE_WAIT_BUDGET_SECONDS']})"
```

**4. Население дыры P1** — мастера, которые продают слоты и получат отказ на
записи. Хост Ayla:

```
python manage.py shell -c "from users.models import SpecialistProfile; qs=SpecialistProfile.objects.filter(status='active', is_available=True, user__is_active=True, is_booking_enabled=False); print('sold_but_unbookable=%d' % qs.count()); [print(str(p.id), p.display_name, str(p.tenant_id)) for p in qs]"
```

**5. Население дыры P3** — строки зеркала бота, которых в фиде Ayla больше
нет. Двумя шагами, потому что базы разные. Хост бота:

```
python manage.py shell -c "from apps.catalog.models import CatalogMaster; from apps.catalog.master_state import AVAILABLE; print('\n'.join('%s %s %s' % (m.id, m.tenant_id, m.name) for m in CatalogMaster.all_tenants.filter(AVAILABLE)))"
```

Хост Ayla (для каждого тенанта из вывода выше):

```
curl -sS -H "Authorization: Bearer $AYLA_INTERNAL_API_TOKEN" "https://api-dev.gobeauty.site/api/v1/internal/specialists/?tenant=<TENANT_UUID>&page_size=200" | python -c "import json,sys; print('\n'.join(r['id'] for r in json.load(sys.stdin).get('results', [])))"
```

Разница множеств = мастера, которых бот продаёт, а Ayla не знает.

**6. Targeted proof для P1** — слоты для мастера с выключенной записью.
Взять `<ID>` из пункта 4; ожидание по коду: **непустой** список слотов
(то есть дефект подтверждён):

```
curl -sS -H "Authorization: Bearer $AYLA_INTERNAL_API_TOKEN" "https://api-dev.gobeauty.site/api/v1/internal/specialists/<ID>/slots/?service_id=<SALON_SERVICE_UUID>&date=$(date -d '+2 days' +%F)"
```

**7. Targeted proof для P6** — форма кода отказа на проводе. Занять слот
любым способом и повторить создание; смотреть **регистр** `error.code`:

```
curl -sS -o - -w '\nHTTP %{http_code}\n' -X POST -H "Authorization: Bearer $AYLA_INTERNAL_API_TOKEN" -H "X-External-User-ID: <EXT_ID>" -H "X-Idempotency-Key: probe-$(date +%s)" -H "Content-Type: application/json" -d '{"client_id":"<CLIENT_UUID>","specialist_id":"<SPEC_UUID>","service_id":"<SVC_UUID>","start_datetime":"<ISO_ЗАНЯТОГО_СЛОТА>","payment_required":false}' "https://api-dev.gobeauty.site/api/v1/internal/appointments/"
```

**8. Строки `SlotConfig` пилотных тенантов** (A6). Хост бота:

```
python manage.py shell -c "from apps.scheduling.models import SlotConfig; [print(str(r.tenant_id), r.slot_granularity_min, r.buffer_min, r.lead_time_min, r.max_advance_days) for r in SlotConfig.all_tenants.all()]"
```

**9. Живость кэша слотов** (пункт 9 раздела 7). Хост Ayla:

```
python manage.py shell -c "from django.core.cache import cache; cache.set('probe:availability', 'ok', 30); print('cache_backend=%s roundtrip=%r' % (type(cache).__name__, cache.get('probe:availability')))"
```

**10. Свежесть зеркала каталога** (верхняя граница O3/O4/P3). Хост бота:

```
python manage.py shell -c "from django.utils import timezone; from apps.tenancy.models import Tenant; [print(str(t.id), t.name, t.last_catalog_sync_ok_at, (timezone.now()-t.last_catalog_sync_ok_at) if t.last_catalog_sync_ok_at else 'NEVER') for t in Tenant.objects.all()]"
```

---

## 9. Точные команды воспроизведения замера

Каждая — одной строкой, из корня `C:/Users/user/PycharmProjects/Ayla/`.

```
git -C ai-bot-platform rev-parse origin/dev
git -C djangoproject-catalog rev-parse origin/dev
git -C djangoproject-catalog show origin/dev:appointments/application/services/availability_query_service.py
git -C djangoproject-catalog show origin/dev:appointments/infrastructure/availability/slot_builder.py
git -C djangoproject-catalog show origin/dev:appointments/infrastructure/availability/providers.py
git -C djangoproject-catalog show origin/dev:appointments/infrastructure/cache/slot_cache.py
git -C djangoproject-catalog show origin/dev:users/specialists_api.py
git -C djangoproject-catalog show origin/dev:users/internal_catalog_api.py
git -C djangoproject-catalog show origin/dev:users/internal_schedule_api.py
git -C djangoproject-catalog show origin/dev:services/service_resolver.py
git -C djangoproject-catalog show origin/dev:appointments/application/services/create_booking_service.py
git -C djangoproject-catalog show origin/dev:appointments/application/services/_booking_guards.py
git -C djangoproject-catalog show origin/dev:appointments/internal_api.py
git -C djangoproject-catalog show origin/dev:djangoProject/exception_handler.py
git -C djangoproject-catalog show origin/dev:core/errors.py
git -C djangoproject-catalog show origin/dev:services/internal_api.py
git -C djangoproject-catalog show origin/dev:appointments/signals.py
git -C djangoproject-catalog grep -n "is_booking_enabled" origin/dev -- '*.py'
git -C djangoproject-catalog grep -n "is_available" origin/dev -- users/tests/test_internal_slots_amd019.py users/tests/test_internal_catalog_1016.py appointments/tests/test_internal_booking_rest_1016.py
git -C ai-bot-platform show origin/dev:apps/miniapp_api/views.py
git -C ai-bot-platform show origin/dev:apps/integrations/ayla/booking_client.py
git -C ai-bot-platform show origin/dev:apps/skills/booking/provider.py
git -C ai-bot-platform show origin/dev:apps/skills/booking/tools.py
git -C ai-bot-platform show origin/dev:apps/bookings/callbacks.py
git -C ai-bot-platform show origin/dev:apps/catalog/master_state.py
git -C ai-bot-platform show origin/dev:apps/catalog/services/upserter.py
git -C ai-bot-platform show origin/dev:apps/catalog/services/http_client.py
git -C ai-bot-platform show origin/dev:apps/scheduling/services/resolver.py
git -C ai-bot-platform show origin/dev:apps/master_api/services/schedule_frame.py
git -C ai-bot-platform show origin/dev:apps/admin_api/services/availability.py
git -C ai-bot-platform show origin/dev:apps/admin_api/services/master_deactivation.py
git -C ai-bot-platform show origin/dev:apps/booking/services/create.py
git -C ai-bot-platform grep -n "BOOKING_VIA_AYLA_REST" origin/dev -- '*.py'
git -C ai-bot-platform grep -n "master_sale_refusal\|execute_confirm" origin/dev -- '*.py'
git -C ai-bot-platform grep -n "slot_unavailable" origin/dev -- apps/miniapp_api/
```

---

## 10. Убрано за собой

**Ничего не создавалось.** Временных файлов, веток, worktree, контейнеров и
процессов не заводилось; ни один тест и ни одна миграция не запускались; в
Linear ничего не писалось. Единственный созданный артефакт — этот файл,
`docs/MEASUREMENT_PILOT_AVAILABILITY.md`, он же результат работы. Рабочие
деревья обоих репозиториев не трогались: чекаут `ai-bot-platform` как стоял
на `feat/recommendation-boundary-client`, так и стоит.
