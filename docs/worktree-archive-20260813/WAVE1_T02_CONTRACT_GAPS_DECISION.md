# WAVE1_T02_CONTRACT_GAPS_DECISION

**Дата:** 2026-08-06
**Предшествует:** `WAVE1_T02_EVENT_CONTRACT_ACK.md` (вердикт `ACK AFTER FIX`)
**Что сделано:** только чтение кода. Код не менялся, PR не создавался, топики не включались, deploy не выполнялся, PR-T02-2 не начат.

---

## 1. Backend Release Revision

| Сторона | Ревизия | Проверено |
|---|---|---|
| Backend (Ayla djangoproject) | `origin/dev` = **`566fe19b19acaf359a94bc2776d1703329f902e7`** | `git rev-parse origin/dev` |
| BOT (ai-bot-platform) | `dev` = **`a7aa237fc50ad7dc6826021a2289bfe41ff425b2`** | squash-merge #1141 |

Проверка наличия требуемых элементов в Backend `566fe19b`:

| Элемент | Есть | Где |
|---|---|---|
| `booking.created` | ✅ | `OutboxEvent.Topic.BOOKING_CREATED` (`appointments/models.py:526`), emit `create_booking_service.py:425` |
| `booking.cancelled` | ✅ | `models.py:528`, emit `cancel_reschedule_service.py:185` |
| `appointment.rescheduled` | ✅ | `models.py:536` («Запись перенесена (canonical)»), emit `cancel_reschedule_service.py:461` |
| dual emission legacy `booking.rescheduled` | ✅ | `models.py:529`, emit `cancel_reschedule_service.py:497`, в той же транзакции, с тем же `correlation_id` |
| `booking.confirmed` | ✅ | `models.py:527`, emit `payments/views.py:791` и `create_booking_service.py:466` |

**Условие годности этого документа.** Всё ниже верно только для `566fe19b`. Локальная рабочая копия Ayla стоит на `feat/memory-foundation-internal-api`, где `appointment.rescheduled` **отсутствует целиком**. Если целевая ревизия пилота — не `origin/dev`, остановиться и пересобрать contract matrix; расхождение по одной этой ветке меняет вердикт по reschedule с «условно годен» на «невозможен».

**OD-T02-4 (требуется):** подтвердить, что Pilot MVP разворачивается с Backend-ревизии, содержащей все пять строк выше, и зафиксировать конкретный commit SHA в runbook. Без этого §7 (порядок включения) не имеет силы.

---

## 2. Status Vocabulary Decision

### Что есть

Producer шлёт `str(appointment.status)` (`create_booking_service.py:430`), значения — `appointments/models.py:25` / `domain/value_objects.py:80-85`:

* `awaiting_payment` — дефолт обычного клиентского пути;
* `confirmed` — walk-in (`dto.confirm_immediately`, #1017).

Контракт `event-contract.md:139`: `status` ∈ {`confirmed`, `pending_payment`, `tentative`}.
BOT `RemoteBookingProxy.Status` (`apps/booking/models.py:774-784`): те же три + `cancelled`, `completed`, `no_show`.

`awaiting_payment` не входит ни туда, ни туда.

### Что происходит сегодня

`handle_booking_created` пишет `data["status"]` дословно (`consumers/booking.py:299`). Поле — `CharField(max_length=20, choices=...)`; Django **не** валидирует `choices` на `.create()`/`.update()`, 16 символов помещаются. Ошибки нет — в зеркало пишется значение вне вокабуляра.

Одно смягчающее обстоятельство: `apps/miniapp_api/views.py:1099` уже содержит защитную строку
`_AYLA_UPCOMING_STATUSES = ("confirmed", "awaiting_payment", "pending_payment")`
с комментарием «defensively so a verbatim-mirrored row still reads as upcoming». То есть дрейф **уже был замечен** и обойдён на уровне одного чтения — но не устранён, и остальные потребители о нём не знают.

### Разбор вариантов

**A. Backend меняет вокабуляр (`awaiting_payment → pending_payment`).**
Плюс: устраняет причину, а не симптом; после этого контракт, producer и consumer говорят одно и то же.
Минус: `awaiting_payment` — это значение доменного `BookingStatus`, оно живёт в state machine, в фильтрах статусов, в API мобильного приложения и в БД (`Appointment.status`). Переименовать «на проводе» — это отдельный маппинг на стороне Ayla (то же, что C, только у producer'а); переименовать в домене — это миграция данных + правки мобильного клиента. Кросс-репозиторная работа, не влезающая в окно пилота.

**B. BOT расширяет публичный enum (добавить `awaiting_payment`).**
Плюс: тривиально.
Минус: закрепляет в публичном контракте BOT два синонима одного состояния навсегда. Каждый будущий потребитель обязан знать оба и сравнивать по обоим. Это не решение, а узаконивание дрейфа — и прямое расширение §3.1, то есть изменение принятого контракта.

**C. BOT нормализует вокабуляр producer'а на ingest.**
Плюс: одна точка изменения (единственное место, где статус приходит от producer'а, — `handle_booking_created`; остальные handler'ы ставят enum-константы сами: `:422` CANCELLED, `:953` COMPLETED, `:1009` CONFIRMED, `:1087` NO_SHOW). Бот-only, влезает в PR-T02-2, не требует координации релизов. Публичный enum BOT остаётся ровно контрактным.
Минус: consumer чинит дефект producer'а — и если это не оформить как временную заплатку, дрейф замораживается.

### Нарушает ли Option C принятый event contract

**Нет.** §3.1 нормирует, что producer обязан прислать одно из трёх значений. Нормализация на входе не меняет ни имя события, ни версию, ни форму payload, ни публичный enum BOT — она приводит нарушение producer'а к контрактному значению до записи. Контракт нарушает уже сегодняшний producer; C восстанавливает соответствие, а не ослабляет его.

Отдельно: §11 требует «default to a safe behavior on unknown enum values» **для открытых** enum'ов (там прямо назван `source`). `status` в §3.1 — закрытый набор, поэтому fail-closed на неизвестном значении статуса контракту не противоречит, а `source` остаётся мягким (`data.get("source", "")`).

### **Решение: Option C — принято**, с обязательным follow-up на Option A

Нормализация — **временный совместимостный слой**, а не постоянный дизайн. В докстринге маппинга фиксируется: «снимается, когда Ayla переименует `awaiting_payment` → `pending_payment`; до тех пор является единственной защитой публичного enum». После правки на стороне Ayla маппинг становится no-op и остаётся как shim.

### Требования владельца — как они выполняются

| Требование | Как |
|---|---|
| Маппинг явный | Отдельный модуль-константа `_PRODUCER_STATUS_MAP = {"awaiting_payment": Status.PENDING_PAYMENT}`, плюс identity-записи для трёх контрактных значений. Никаких эвристик, никакого `.replace()` |
| Неизвестный статус fail-closed | Собственное исключение `UnknownBookingStatusError(ValueError)` → `HANDLER_EXCEPTION` → 500 → ретраи → dead-letter. Событие **не** подтверждается: строка `IngestDedupe` откатывается вместе с транзакцией handler'а, поэтому ретрай Ayla после исправления применится начисто |
| Raw producer value сохраняется | **Только в лог**, без изменения схемы: строка `eventbus.consumer.booking.created.status_normalized raw=%s normalized=%s appointment_id=%s`, значение через санитайзер (`_safe_log_value`-класс — значение приходит от держателя HMAC-секрета). Новое поле `raw_status` потребовало бы миграцию, а миграции в T-02 идут через отдельный tech-lead-гейт; канонический raw и так лежит в `OutboxEvent.payload` на стороне Ayla и в `IngestDLQ.raw_body` на нашей |
| User-facing lookup использует normalized | `_AYLA_UPCOMING_STATUSES` в `miniapp_api/views.py:1099` схлопывается до контрактных значений: защитный `"awaiting_payment"` удаляется, потому что после нормализации такого значения в зеркале появиться не может. Это и есть проверка, что нормализация работает: если строка вернётся — значит, где-то есть путь записи в обход |
| Тесты покрывают все допустимые значения | Параметризованный тест на все три контрактных (`confirmed`, `pending_payment`, `tentative`) + `awaiting_payment` → `pending_payment` + два негативных: произвольная строка и пустая строка ⇒ `HANDLER_EXCEPTION`, proxy не создан/не изменён, `IngestDedupe` для этого `event_id` отсутствует. Плюс регрессионный тест, что `_AYLA_UPCOMING_STATUSES` не содержит `awaiting_payment` |
| Нет silent acceptance произвольных строк | Обеспечивается fail-closed выше. Сегодняшнее поведение (пишем что прислали) исчезает |

**Побочный эффект, который надо принять сознательно.** После включения fail-closed любое новое значение статуса, которое Ayla добавит в `BookingStatus` и пришлёт, остановит доставку `booking.created` для затронутых записей (ретраи → dead-letter), вместо тихой записи мусора. Это осознанный выбор: закрытый enum на закрытом наборе событий. Отсюда требование к Ayla — предупреждать о расширении `BookingStatus` до релиза (см. §11).

---

## 3. Booking Confirmation Lifecycle

### Фактический жизненный цикл

```
CreateBookingService
├── dto.confirm_immediately = False  (обычный клиент, дефолт)
│     Appointment.status = awaiting_payment           value_objects.py:100
│     emit booking.created (status=awaiting_payment)  create_booking_service.py:425
│     Payment(status=pending) создаётся               :291
│        │
│        ├── YooKassa webhook payment.waiting_for_capture
│        │     → Appointment → CONFIRMED
│        │     → emit booking.confirmed {appointment_id, client_id,
│        │                               specialist_id, payment_id}
│        │       payments/views.py:786-802   actor="system"
│        │
│        ├── YooKassa webhook payment.canceled
│        │     → emit payment.failed        :838
│        │     → Appointment ОСТАЁТСЯ awaiting_payment
│        │
│        └── клиент просто не платит
│              → Appointment ОСТАЁТСЯ awaiting_payment НАВСЕГДА
│
└── dto.confirm_immediately = True  (walk-in провайдера, #1017)
      Appointment.status = confirmed
      emit booking.created (status=confirmed)   :425
      emit booking.confirmed                    :466   ← в той же транзакции
```

### Ответы на поставленные вопросы

**Когда Backend эмитит `booking.confirmed`.** Два места: `payments/views.py:791` — на webhook `payment.waiting_for_capture`, то есть **в момент постановки холда, а не захвата денег**; и `create_booking_service.py:466` — сразу за `booking.created` для walk-in.

**Всегда ли после оплаты.** Формулировка неточна в обе стороны. Событие приходит на *авторизацию холда*, а не на оплату; захват денег даёт отдельный `payment.captured` (`views.py:810`), который статус записи уже не двигает. Комментарий в коде (`:787-788`) прямо утверждает: «payment hold is the only path in this codebase that moves an appointment to CONFIRMED».

**Может ли запись остаться `awaiting_payment` навсегда.** **Да.** Полный список beat-задач Ayla (`CELERY_BEAT_SCHEDULE` в `djangoProject/settings/base.py`) — 14 штук: outbox dispatch, outbox publish, reminders, aftercare, `reconcile_captures`, биллинг ×2, water ×2, beauty insights, nutrition ×2, `infer_user_patterns`, `purge_expired_idempotency_keys`. **Задачи, отменяющей неоплаченную запись, нет.** Есть `expires_at` у холда YooKassa, но он живёт на платеже и на статус `Appointment` не влияет. Слот при этом остаётся занятым: `AWAITING_PAYMENT` входит в `ACTIVE_BOOKING_STATUSES` (`value_objects.py:110-112`), то есть блокирует конфликтующие брони.

**Что при отмене до оплаты.** Переход `AWAITING_PAYMENT → CANCELLED` разрешён state machine (`value_objects.py:100`), идёт обычным `CancelBookingService` и даёт обычный `booking.cancelled`. Отдельного события нет, специальной обработки не нужно.

**Timeout / expiry.** Отсутствует на уровне записи. Единственный таймер — `yookassa_expires_at` на платеже.

**Какой status должен быть в `RemoteBookingProxy`.** `pending_payment` после нормализации из §2 — до прихода `booking.confirmed`; `confirmed` — после. Для walk-in `confirmed` приходит сразу двумя событиями подряд, оба идемпотентны.

**Входит ли `booking.confirmed` в controlled pilot.** Обязан входить — см. решение.

### **OD-T02-5 (требуется владелец): включить `booking.confirmed` в пилотный набор**

Рекомендация: **включить**. Обоснование:

1. Без него зеркало BOT остаётся в `pending_payment` **навсегда** для каждой обычной записи — не «до какого-то момента», а буквально навсегда, потому что второго пути в `confirmed` нет и таймера тоже нет.
2. Ayla-сторона это уже зафиксировала как дефект: комментарий у emit — «Without this emit, bot-platform memory drifts ("your booking is awaiting payment" days after hold landed)». То есть producer писался в расчёте на то, что consumer это событие получает.
3. Это входные данные для §4 (напоминания только для подтверждённых) — без `booking.confirmed` правило «только confirmed» вырождается в «никогда».
4. Стоимость нулевая: handler `handle_booking_confirmed` (`consumers/booking.py:966`) уже написан, зарегистрирован на v1 (`:1109`), покрыт тестами, идемпотентен и умеет ставить payment-mirror на `payment_id`. Требуется только добавить имя в `EVENT_INGEST_ALLOWED_EVENTS` и в топики Ayla.

Отвергнутые варианты: «не использовать awaiting-payment flow» — это изменение продуктового флоу оплаты, вне T-02; «считать created финальным» — заведомая ложь в зеркале, ломает §4 и Records UX.

**Известный дефект, вскрытый при разборе (важен для §7).** `handle_booking_confirmed` при отсутствующем proxy молча ничего не делает: `_assert_proxy_tenant` на `proxy=None` возвращается без ошибки (`:141-142`), затем `.filter(...).update(...)` совпадает с 0 строк, handler завершается штатно → диспетчер **фиксирует строку `IngestDedupe`** → подтверждение **теряется безвозвратно**. Тот же дефект у `handle_booking_cancelled` (`:411-419`): его комментарий обещает «awaiting Ayla retry», но ретрая не будет — handler вернул успех, publisher пометил строку `sent`. Единственный handler, ведущий себя правильно в этой ситуации, — canonical reschedule: он **поднимает** `CanonicalReschedulePendingProxyError`, и именно поэтому его событие переживает окно.

---

## 4. Reminder Eligibility

### Текущие пути

| Что | Где | Поведение |
|---|---|---|
| Создание | `consumers/booking.py:334-340` → `_schedule_reminders` (`:166`) | На `booking.created`, **безусловно по статусу**. Единственные условия — `bot_user is not None` и непустой `chat_id`. Две строки T-24h/T-2h, `status=PENDING`, идемпотентно через `update_or_create` |
| Для `awaiting_payment` | — | **Да, создаются.** Проверки статуса в коде нет вообще |
| Обновление на `booking.confirmed` | `consumers/booking.py:978-979` | **Нет.** Докстринг прямо: «No reminder change — confirming doesn't move `start_at`» |
| Гашение на cancel | `_cancel_reminders` (`:211`) | Да, `PENDING → CANCELLED`, идемпотентно. **Но только если proxy существует** — при cancel-before-created handler выходит раньше (`:411-419`) |
| Перенос на reschedule | `_reschedule_reminders` (`:224`) | Пере-привязка `visit_at`/`scheduled_at` для PENDING |
| Отправка | `apps/bookings/tasks.py`, beat каждые 15 мин | Забирает PENDING с наступившим `scheduled_at` |
| Проверка состояния перед отправкой | `apps/bookings/recheck.py:43` `_recheck_booking_state` | **Для Ayla-строк не работает** |

### Ключевой факт

`_recheck_booking_state` классифицирует по `BookingReminder.booking_request` — модели YClients-эпохи. У строк, созданных из eventbus, этот FK **NULL** (связь идёт через `ayla_appointment_id`), и классификатор возвращает `(_ACTION_SEND, "null_fk_legacy_or_ayla_path")` — `recheck.py:67-69`, с комментарием «NULL FK = legacy row OR Ayla-path. Pilot-scope gap». `RemoteBookingProxy` в проверке не участвует вовсе.

**Итог: напоминание по неоплаченной записи не просто может уйти — оно уйдёт гарантированно, и защиты на момент отправки нет.** Сценарий: клиент завёл запись на завтра, не заплатил, слот занят; за 24 часа бот пишет «Напоминаю о записи завтра». Отменить эту запись некому — expiry нет (§3).

### **OD-T02-6 (требуется владелец): напоминания только для confirmed**

Рекомендация: **«reminders только для confirmed»**, реализация — вариант «не создавать, пока не подтверждено»:

* в `handle_booking_created` вызывать `_schedule_reminders` только если нормализованный статус == `confirmed` (walk-in попадает сюда сразу);
* в `handle_booking_confirmed` добавить вызов `_schedule_reminders` — на этом шаге известны и `start_at`, и `end_at` из proxy;
* `_schedule_reminders` идемпотентен по `(ayla_appointment_id, tenant, kind)`, поэтому повторная доставка любого из двух событий не плодит строк — walk-in, где оба события приходят подряд, отработает корректно.

Почему не «создавать disabled и активировать после `booking.confirmed`»: в `BookingReminder.Status` нет подходящего значения (`PENDING`, `SENT_NO_REPLY`, `SENT`, `CONFIRMED`, `RESCHEDULE_REQUESTED`, `CANCELLED`, `ESCALATED`, `FAILED`, `STALE_DROPPED` — `apps/booking/models.py:502-518`), потребуется новое значение или новое поле, то есть миграция и tech-lead-гейт. При этом «не создавать» даёт ровно тот же наблюдаемый результат.

Почему не «reminders disabled for pilot»: напоминания — заявленная ценность пилота, а OD-T02-3 уже отложил их до PR-T02-4; глушить их ещё и флагом означает выкинуть функциональность там, где достаточно условия.

**Строго обязательное следствие:** OD-T02-6 **не имеет смысла без OD-T02-5**. Если `booking.confirmed` не включён, «только confirmed» = «никогда» для всех записей, кроме walk-in. Два решения принимаются вместе или не принимаются вовсе.

**Follow-up (PR-T02-4, не сейчас):** научить `_recheck_booking_state` смотреть на `RemoteBookingProxy` для строк с `booking_request is None` — это защита на момент отправки, вторая линия к правилу создания. Сейчас туда не лезем: модуль вне scope T-02 и его трогает поток напоминаний.

---

## 5. Tenant ID Preflight

### Существенная поправка к предыдущему отчёту

В `WAVE1_T02_EVENT_CONTRACT_ACK.md` §3.4 и F-3 я записал `tenant_id: null` как блокирующий риск, опираясь на докстринг `safe_tenant_id` («the FK is null=True until backfill completes»). **Этот докстринг устарел.** На `566fe19b`:

* `Appointment.tenant` — `ForeignKey('tenants.Tenant', on_delete=PROTECT)`, **без `null=True`** (`appointments/models.py`);
* миграция `0009_appointment_tenant_not_null.py` содержит три вещи: guard-функцию, которая **отказывается применяться**, если остались строки с `tenant IS NULL` (`:28-36`), `AlterField` на `null=False` (`:64`) и `AddConstraint(CheckConstraint(Q(tenant__isnull=False)))` (`:74`);
* предшествующая `0008_backfill_appointment_tenant.py` — сам бэкфилл.

**Вывод: на уровне схемы `tenant_id IS NULL` для `Appointment` невозможен, и повторное появление NULL после бэкфилла исключено конструктивно** (NOT NULL + CheckConstraint, а не только код приложения). F-3 из предыдущего отчёта отзывается как блокер и понижается до пункта верификации.

### Что всё ещё нужно проверить и почему

Гарантию даёт **применённая** миграция, а не миграция в репозитории. Проверяем факт применения в целевой среде, а не пишем бэкфилл.

| Проверка | Запрос / действие | Ожидание |
|---|---|---|
| Canonical pilot tenant UUID | **Из кода не выводится.** Запросить у Ayla/ops, hyphenated lower-case, в том виде, в каком уходит в конверте | одно значение, зафиксировано в runbook |
| Тот же UUID существует в БД BOT | `SELECT id, slug FROM tenancy_tenant WHERE id = '<uuid>'` (менеджер `Tenant.objects` — active-only, отключённый тенант читается как not-found, `ingest_tenancy.py:266-270`) | 1 строка, активна |
| Миграция 0009 применена | `SELECT name FROM django_migrations WHERE app='appointments' AND name LIKE '0009%'` | 1 строка |
| Appointment с NULL | `SELECT count(*) FROM appointments_appointment WHERE tenant_id IS NULL` | `0` (иначе 0009 не применена) |
| Outbox-строки включённых топиков с NULL в конверте | `SELECT count(*) FROM appointments_outboxevent WHERE external_delivery_enabled = true AND payload->>'tenant_id' IS NULL` | `0` |
| Источники потенциальных NULL | `CreateBookingService` ставит `tenant_id=specialist.tenant_id` (`:271`); значит нужен ещё `SELECT count(*) FROM users_specialistprofile WHERE tenant_id IS NULL` — мастер без тенанта не даст создать запись, но упадёт при создании, а не при доставке | `0` для пилотного салона |

Про «источники таких строк» по существу: единственный способ получить NULL — вставка в обход `CreateBookingService` (Django-admin, raw SQL, миграция данных). Комментарий у поля прямо это фиксирует: «admin/raw inserts now hit the constraint instead of silently landing in the gap». После 0009 такая вставка падает на constraint, а не проходит тихо.

Про outbox: `external_delivery_enabled` проставляется **в момент emit** из `OUTBOX_EXTERNAL_DELIVERY_TOPICS` (`envelope.py:248-252`). Строки, созданные до flip'а, навсегда остаются с `False`, поэтому во внешнюю доставку попадают только строки, созданные после — то есть заведомо после 0009. Счётчик выше должен быть `0` по построению; если он не `0`, значит кто-то делал ручной `UPDATE` флага, и это надо расследовать до flip'а.

**Бэкфилл на этой фазе не выполняется** — и, по итогам разбора, не требуется: он уже сделан миграцией 0008. Если какая-то из проверок вернёт не `0`, это означает, что целевая среда не на `566fe19b`, и мы возвращаемся к §1.

### Критерий до flip

```
migration 0009 applied                                   = true
appointments_with_null_tenant                            = 0
outbox_rows_for_enabled_topics_with_null_tenant          = 0
pilot tenant UUID exists and is active in BOT tenancy    = true
```

Ни одна из этих проверок не выполнена — доступа к БД у меня нет. Это операционный чек-лист, а не результат.

---

## 6. Final Pilot Topic Set

Включаем четыре. `booking.rescheduled` — **не включаем**.

### 6.1 `booking.created`

| | |
|---|---|
| Producer | `appointments/application/services/create_booking_service.py:425` |
| event_version | 1 (`envelope.py:60`) |
| Required payload (жёсткое чтение BOT) | `appointment_id`, `start_at`, `end_at`, `status` |
| Optional | `service_id`, `specialist_id`, `source`, `client_id`, `price_total`, `payment_id`, `amount`, `specialist_timezone` |
| BOT handler | `handle_booking_created` (`consumers/booking.py:264`), v1 |
| Normalized status | `awaiting_payment` → `pending_payment`; `confirmed`/`pending_payment`/`tentative` — identity; иное → `UnknownBookingStatusError` → 500 |
| Ordering dependency | Нет. Корень всей цепочки |
| Enable order | **1** |
| Rollback | Удалить имя из `EVENT_INGEST_ALLOWED_EVENTS` (BOT) и из `OUTBOX_EXTERNAL_DELIVERY_TOPICS` (Ayla). Убирать сначала на Ayla: обратный порядок даёт окно, в котором bot отвергает уже отправленные события с 500 |

### 6.2 `booking.confirmed`

| | |
|---|---|
| Producer | `payments/views.py:791` (хук `payment.waiting_for_capture`) и `create_booking_service.py:466` (walk-in) |
| event_version | 1 (`envelope.py:61`) |
| Required payload | `appointment_id` |
| Optional | `payment_id` (ставит payment-mirror `capture_state=authorized`), `amount`, `client_id`, `specialist_id` |
| BOT handler | `handle_booking_confirmed` (`:966`), v1 |
| Normalized status | Producer статус не шлёт; handler ставит константу `Status.CONFIRMED` |
| Ordering dependency | **Требует существующий proxy.** При его отсутствии — тихая потеря с фиксацией dedupe (см. §3) |
| Enable order | **2** |
| Rollback | Как выше. Зеркало замирает в `pending_payment`; при OD-T02-6 напоминания просто не создаются — деградация тихая, но безопасная |

### 6.3 `booking.cancelled`

| | |
|---|---|
| Producers | `cancel_reschedule_service.py:185`; `appointments/views.py:606` (ветка no-show, после `booking.no_show`); каскад ухода мастера через тот же сервис |
| event_version | 1 (`envelope.py:62`) |
| Required payload | `appointment_id` |
| Optional | `cancelled_by` ∈ {user, master, system}, `reason_code`, `start_at`, `specialist_id`, `cancelled_at`, `initiator_role`, `refund_percent`, `reason` |
| BOT handler | `handle_booking_cancelled` (`:366`), v1 |
| Normalized status | Константа `Status.CANCELLED` |
| Ordering dependency | **Требует существующий proxy** — иначе тихий дроп с фиксацией dedupe |
| Enable order | **3** |
| Rollback | Как выше. Осторожно: откат оставляет отменённые записи в зеркале активными и с живыми напоминаниями. Требуется ручная сверка после отката |
| Особое условие | `booking.no_show` **не включать**: путь no-show эмитит два события подряд, из них принимаем только второе |

### 6.4 `appointment.rescheduled`

| | |
|---|---|
| Producer | `cancel_reschedule_service.py:461` |
| event_version | 1 (`envelope.py:67`) |
| Required payload | `appointment_id`, `version`, `previous_version`, `revision_id`, `changed_fields`, `actor` (`_CANONICAL_REQUIRED_FIELDS`, `consumers/booking.py:586`) |
| Optional | `starts_at`, `previous_starts_at` + необязательные extras producer'а |
| BOT handler | `handle_appointment_rescheduled_canonical` (`:684`), v1 |
| Normalized status | Статус не трогает |
| Ordering dependency | **Требует существующий proxy**, но здесь это безопасно: поднимает `CanonicalReschedulePendingProxyError` → 500 → ретраи → dead-letter, дедупа не фиксируется |
| Enable order | **4** |
| Rollback | Как выше |
| Особое условие | `booking.rescheduled` **обязан отсутствовать** в `OUTBOX_EXTERNAL_DELIVERY_TOPICS`. Включение обоих даёт dead-letter на каждый перенос. Внутренние обработчики Ayla от этого не пострадают — они не зависят от `external_delivery_enabled` |

---

## 7. Enable Order

### Предложенный владельцем порядок отклоняется

Заявленная последовательность — `cancelled → created → confirmed → rescheduled`. **Первый шаг вреден.**

Если включить `booking.cancelled` первым, в БД BOT нет ни одного `RemoteBookingProxy` (их создаёт только `booking.created`). Каждый `booking.cancelled` попадёт в ветку `:411-419`: handler залогирует `out_of_order_dropped` и вернётся штатно ⇒ диспетчер зафиксирует `IngestDedupe` ⇒ publisher получит 200 и пометит строку `sent`. Комментарий в коде обещает «awaiting Ayla retry post-created» — **ретрая не будет**: успешный ответ ретраев не порождает. Дальше, когда включат `created`, тот же `event_id` уже в dedupe: даже ручной реплей вернёт `DUPLICATE`.

Итог первого шага: отменённые записи, чьи отмены пришли в окно, останутся в зеркале активными **навсегда**, с живыми напоминаниями. Это ровно тот вред, ради предотвращения которого затевается контроль.

### Правильный порядок

| # | Topic | Зависимость | Что проверяем перед следующим шагом |
|---|---|---|---|
| 1 | `booking.created` | нет | строки `eventbus.ingest.tenant_verify_accepted verification_mode=pilot_allowlist event_name=booking.created`; появление `RemoteBookingProxy` со статусом `pending_payment`/`confirmed`; отсутствие `UnknownBookingStatusError`; отсутствие 5xx |
| 2 | `booking.confirmed` | proxy от шага 1 | переходы `pending_payment → confirmed`; при OD-T02-6 — появление двух `BookingReminder` именно здесь, а не на шаге 1 |
| 3 | `booking.cancelled` | proxy от шага 1 | `status=cancelled`; PENDING-напоминания погашены; **ноль** строк `out_of_order_dropped` — любая такая строка означает безвозвратно потерянную отмену |
| 4 | `appointment.rescheduled` | proxy от шага 1 | сдвиг `start_at`/`end_at` с сохранением длительности; `last_applied_appointment_version` растёт; **ноль** `CanonicalReschedulePendingProxyError` |

Шаги 2 и 3 независимы друг от друга и могут идти одним изменением; шаг 1 обязан быть раньше обоих, шаг 4 — последним. Между шагами — выдержка не меньше суток, чтобы через окно прошли реальные записи, а не только синтетика.

### Cancel-before-created

Обрабатывается неправильно (тихий дроп с фиксацией dedupe вместо ретрая). В рамках порядка выше окно закрывается **процедурно**: шаг 1 идёт первым, и к моменту шага 3 proxy для всех новых записей уже существуют.

Остаточный случай — отмена записи, созданной **до** flip'а (см. ниже). Он не закрывается порядком. Варианты: (а) принять и мониторить через `out_of_order_dropped` — счётчик этих строк и есть точный список потерянных отмен; (б) починить handler так, чтобы он поднимал исключение вместо тихого возврата, симметрично canonical reschedule. Вариант (б) правильнее по существу, но превращает каждую отмену пред-flip записи в 4.5 часа ретраев и dead-letter, то есть меняет тихую потерю на шумную. **Рекомендация: (а) на пилот с явным мониторингом, (б) — отдельным решением после пилота.** Тихую потерю выбираем только потому, что она ограничена конечным, известным множеством пред-flip записей.

### Replay старых outbox-строк после flip

`external_delivery_enabled` проставляется **в момент emit** (`envelope.py:248-252`), publisher выбирает только `external_delivery_enabled=True` (`publisher.py:404-409`). Отсюда:

* **строки, созданные до flip'а, не будут доставлены никогда** — их флаг зафиксирован в `False`;
* значит, для всех записей, существовавших до flip'а, в BOT **не появится proxy**;
* следовательно: их отмена → тихий дроп (см. выше), их перенос → `PendingProxyError` → 4.5 ч ретраев → dead-letter **гарантированно, на каждую такую запись**;
* «включить задним числом» технически возможно (комментарий в `settings/base.py`: «existing rows can be flipped via a one-off SQL UPDATE»), но это отправит в bot всю накопленную историю топика разом — реплей неизвестного объёма в свежую систему. **Не рекомендуется.**

**Решение: принять окно.** Пред-flip записи пилотного салона в зеркале BOT не существуют и не появятся. Практические следствия — сформулировать для дежурного до flip'а:

1. Дежурный обязан знать, что `PendingProxyError` в первые недели — **ожидаемый** класс, а не инцидент, если `appointment_id` относится к пред-flip записи. Иначе первый же dead-letter поднимет ложную тревогу.
2. Идеальное окно для flip'а — время, когда доля будущих визитов, заведённых до flip'а, минимальна. Практически это означает включаться как можно раньше и не тянуть, потому что каждая неделя ожидания увеличивает хвост пред-flip записей, чьи переносы будут dead-letter'иться.

---

## 8. Retry/DLQ Reality

Ниже — поведение **по коду**. Контрактные §6.3/§6.4 расходятся с ним и подлежат правке (см. §9).

### 8.1 Publisher (Ayla, `appointments/infrastructure/outbox/publisher.py`)

| Параметр | Код | §6.3/§6.4 контракта |
|---|---|---|
| Max attempts | **8** (`MAX_DELIVERY_ATTEMPTS`, `:102`) | 5 |
| Backoff | `2^attempt × 30s`, потолок **1 час** (`:106-107`) | 1s, 5s, 30s, 120s, 600s |
| Суммарно до dead-letter | **≈4.5 часа**, dead на 9-й попытке (`:53-56`) | не указано |
| Dead-letter | `bot_delivery_status='dead'`, `bot_dead_lettered_at` | `dead=true` |
| Severity | **`logger.warning("outbox.publisher.dead_lettered …")`** (`:425`) | «alert fires» |
| PagerDuty | **в коде отсутствует.** Нет интеграции, нет вызова алерта — только строка лога | «PagerDuty rotation `ayla-events`» |
| Replay | `manage.py replay_dead_outbox_events` (C5) | «on-call investigates, manually re-publishes» |
| Порядок выборки | `order_by("created_at")`, батч 50, `select_for_update(skip_locked=True)` (`:404-415`) | — |
| Каденция | Celery beat `publish_outbox_events_to_bot`, каждые 30 с | — |

**Практический вывод, который меняет план.** Я ранее писал, что отказ «запейджит дежурного». По коду это **неверно**: dead-letter пишет `warning` в лог, и всё. Пейджинг существует только если в системе логов настроено правило на `outbox.publisher.dead_lettered` — это надо проверить и, если правила нет, завести **до** flip'а. Иначе первые dead-letter'ы никто не увидит, и деградация будет тихой — что хуже ложной тревоги.

### 8.2 Классификация ошибок

**400 — terminal, немедленный dead-letter без ретраев** (publisher: любой 4xx кроме 429 ⇒ `dead` сразу, `:33-36`):

| Причина | Где |
|---|---|
| `tenant_id: null` у tenant-scoped события | `ingest_envelope.py:225` |
| `user_id` пустой или не строка | `:229` |
| `event_name` вне `ALLOWED_EVENT_NAMES` | `:199` |
| `actor` вне {system, user, admin} | envelope parser |
| `occurred_at` не ISO8601 / без таймзоны | `:220` |
| `data` не объект | envelope parser |

**401 — terminal:** битая HMAC-подпись (`views.py:193`).

**422 — terminal, + строка `IngestDLQ`:**

| Причина | Где |
|---|---|
| Имя известно, версия не зарегистрирована | `views.py:279-291` |
| `event_id` длиннее 36 символов | `views.py:294-311` |
| Имя вне `_KNOWN_NAMES` (при рассинхроне с parser-allowlist) | `ingest_dispatcher.py:234` |

**429 — retryable** (rate limit, `views.py:168`); publisher трактует как транзиентный.

**500 — retryable, 8 попыток, ~4.5 ч, затем dead:**

| Причина | Где |
|---|---|
| Тенант или событие не в allowlist | `ingest_tenancy.py:558` → `HANDLER_EXCEPTION` → `views.py:361` |
| Тенант в allowlist, но нет активной строки `Tenant` в БД BOT | `ingest_tenancy.py:282` reason `tenant_not_found` |
| Ошибка БД при проверке тенанта | reason `tenant_lookup_error` — fail-closed |
| Кросс-тенантный спуфинг `appointment_id` | `consumers/booking.py:154` |
| Отсутствует обязательное поле payload (`KeyError`) | любой handler |
| **Неизвестный статус** (после §2) | новый `UnknownBookingStatusError` |
| canonical reschedule без proxy | `CanonicalReschedulePendingProxyError` |
| Разрыв версий canonical | `CanonicalRescheduleVersionGapError` |

**503 — retryable:** перегрузка ingest, с `Retry-After` (`views.py:338`).

**200, но событие потеряно — отдельный класс, которого нет в контракте:**

| Причина | Где |
|---|---|
| `booking.cancelled` до `booking.created` | `consumers/booking.py:411-419` |
| `booking.confirmed` до `booking.created` | `:994-1011` (0 строк в UPDATE) |
| Неизвестный тенант в конверте (`_resolve_tenant` → None) | `:280-285`, `:379-385`, `:986-992` — ранний `return` |

Этот класс опаснее ретраев: publisher видит успех, `IngestDedupe` зафиксирован, повторная доставка вернёт `DUPLICATE`. Единственный след — строка лога. Отсюда требование мониторинга в §7 и §11.

### 8.3 Что из этого следует для мониторинга

До flip'а должны существовать правила на:

1. `outbox.publisher.dead_lettered` (Ayla) — иначе dead-letter невидим;
2. `eventbus.ingest.tenant_verify_rejected` (BOT) — отказ allowlist'а, первый признак рассинхрона конфигов;
3. `eventbus.consumer.booking.cancelled.out_of_order_dropped` (BOT) — **каждая строка = безвозвратно потерянная отмена**;
4. `eventbus.consumer.booking.*.unknown_tenant` (BOT) — то же для неизвестного тенанта;
5. `CanonicalReschedulePendingProxyError` в `HandlerFailureTracker` — ожидаемый шум по пред-flip записям, надо отделять от настоящих сбоев.

---

## 9. Required PRs

| PR | Репозиторий | Содержание | Гейт |
|---|---|---|---|
| **PR-T02-2** | BOT | §2: модуль маппинга статусов + вызов в `handle_booking_created` + `UnknownBookingStatusError` + forensic-лог raw-значения + чистка `_AYLA_UPCOMING_STATUSES`. §4: условие создания напоминаний в `handle_booking_created` + вызов `_schedule_reminders` в `handle_booking_confirmed`. Тесты по чек-листу §2 и §4. **Миграций нет** | OD-T02-5 + OD-T02-6 |
| **PR-T02-2-docs** | BOT | `event-contract.md`: §3.1 — заметка об отклонении producer'а (`awaiting_payment`) по паттерну «Implementation deviations»; §6.3/§6.4 — привести к коду publisher'а (8 попыток, `2^n×30s`, потолок 1 ч, ~4.5 ч, `logger.warning`, отсутствие PagerDuty-интеграции); новый подраздел про класс «200, но потеряно» | нет |
| **PR-T02-3** | BOT | `.env.staging.template` + runbook активации: четыре имени, порядок включения §7, критерии §5, правила мониторинга §8.3, инструкция дежурному про ожидаемые `PendingProxyError` по пред-flip записям | после PR-T02-2 |
| **PR-T02-4** | BOT | Напоминания по OD-T02-3 (вне этого документа) + follow-up: `_recheck_booking_state` учит `RemoteBookingProxy` для строк с `booking_request is None` | отдельно |
| **Ayla-1** | Ayla | Переименование `awaiting_payment` → `pending_payment` на проводе. После него маппинг §2 становится no-op и остаётся как shim | post-pilot |
| **Ayla-2** | Ayla | Снятие legacy `booking.rescheduled` после миграции BOT на canonical | post-pilot |
| **Ops** | — | Проверки §5 (без бэкфилла), правила алертинга §8.3, согласование содержимого `OUTBOX_EXTERNAL_DELIVERY_TOPICS` в письменном виде | до flip'а |

Порядок выкладки на flip: PR-T02-2 → PR-T02-2-docs → PR-T02-3 → deploy BOT → проверки §5 → включение топиков Ayla по §7.

---

## 10. Owner Decisions

| ID | Вопрос | Рекомендация | Статус |
|---|---|---|---|
| **OD-T02-4** | Целевая Backend-ревизия пилота содержит все пять элементов §1 | Зафиксировать SHA `566fe19b` (или новее с теми же элементами) в runbook | **требуется** |
| **OD-T02-5** | Включать ли `booking.confirmed` | **Включить.** Иначе зеркало навсегда в `pending_payment`: второго пути в `confirmed` нет, expiry нет. Handler готов, стоимость — одна строка конфигурации | **требуется** |
| **OD-T02-6** | Правило напоминаний | **Только для confirmed**, реализация «не создавать до подтверждения» (без миграции). Принимается только вместе с OD-T02-5 | **требуется** |
| **OD-T02-7** | Cancel-before-created для пред-flip записей | Принять тихий дроп с мониторингом `out_of_order_dropped`; переход на fail-loud — отдельным решением после пилота | **требуется** |
| Gap 1 | Вокабуляр статусов | **Option C принят** (не нарушает контракт, обоснование §2) с обязательным follow-up Ayla-1 | принято, ратификация владельцем при необходимости |
| Gap 5 | Пилотный набор топиков | Четыре имени §6, `booking.rescheduled` исключён, `booking.no_show` исключён | вытекает из OD-T02-5 |

---

## 11. Final ACK Conditions

### Статус: **READY AFTER OWNER DECISION**

Не `READY FOR PR-T02-2`, потому что содержание PR-T02-2 определяется OD-T02-5 и OD-T02-6: без них неизвестно, ставить ли условие на создание напоминаний и добавлять ли четвёртое имя.
Не `BLOCKED BY BACKEND CONTRACT`, потому что ни одна оставшаяся проблема не требует изменений на стороне Backend до flip'а: вокабуляр закрывается на стороне BOT (§2), tenant-риск закрыт миграцией 0009 (§5), двойная эмиссия закрывается конфигурацией (§6.4).

### Условия перехода в `READY FOR PR-T02-2`

1. **OD-T02-4** — зафиксирована целевая Backend-ревизия.
2. **OD-T02-5** — решение по `booking.confirmed`.
3. **OD-T02-6** — решение по напоминаниям.
4. **OD-T02-7** — решение по cancel-before-created.

### Условия flip'а (после мержа PR-T02-2/-2-docs/-3)

5. Письменный ACK Ayla по точному содержимому `OUTBOX_EXTERNAL_DELIVERY_TOPICS` — четыре имени, **без** `booking.rescheduled` и **без** `booking.no_show`.
6. Canonical UUID пилотного тенанта получен и подтверждён присутствующим и активным в `tenancy_tenant` БД BOT.
7. Проверки §5 выполнены с нулевыми результатами.
8. Правила алертинга §8.3 заведены — в первую очередь на `outbox.publisher.dead_lettered`, которого сегодня нет нигде, кроме строки лога.
9. Обязательство Ayla предупреждать о расширении `BookingStatus` до релиза (следствие fail-closed из §2).
10. Согласовано с дежурным Ayla, что rollback имеет то же свойство 500 → ретраи → dead и выполняется в обратном порядке: сначала топики Ayla, затем allowlist BOT.

### Что осталось невыполненным и почему

Проверки §5 и содержимое `OUTBOX_EXTERNAL_DELIVERY_TOPICS` — это доступ к БД и к среде развёртывания, которого у меня нет. Всё, что можно было установить чтением кода, установлено; остальное оформлено как исполняемый чек-лист, а не как утверждение.

---

**Остановка после отчёта.** PR-T02-2 не начат, топики не включены, deploy не выполнялся, код не менялся.
