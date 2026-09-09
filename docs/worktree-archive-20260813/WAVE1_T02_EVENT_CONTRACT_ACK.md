# WAVE1_T02_EVENT_CONTRACT_ACK

**Дата:** 2026-08-06
**Цель:** подтвердить официальный event contract для трёх Pilot MVP операций (create / reschedule / cancel) перед включением Backend external delivery.
**Статус кода:** ничего не менялось, PR не создавался, флаги не трогались.

## База доказательств

| Сторона | Что читалось | Ревизия |
|---|---|---|
| Backend (Ayla djangoproject) | `origin/dev` | `566fe19b` |
| BOT (ai-bot-platform) | worktree `ai-bot-platform-t02`, ветка `dev` | `a7aa237` (после #1140 + #1141) |

⚠️ **Важно про рабочую копию Ayla.** Локальный чекаут `C:\Users\user\PycharmProjects\Ayla\djangoproject` стоит на ветке `feat/memory-foundation-internal-api`, где `appointment.rescheduled` **не существует вообще** — там reschedule эмитит только legacy `booking.rescheduled`. Весь анализ ниже сделан по `origin/dev`. Если пилот будет разворачиваться не с `dev`, все выводы по reschedule недействительны.

---

## 1. Backend Producer Matrix

Общий механизм: `appointments/infrastructure/outbox/envelope.py::emit_outbox_event` — пишет строку `OutboxEvent` в той же транзакции, что и доменное изменение; конверт по ADR-0009 (`event_id` = PK строки, `event_version` из реестра `EVENT_VERSIONS`, `correlation_id`, `tenant_id`, `user_id`, `actor`).

Внешняя доставка гейтится **дважды**: полем `OutboxEvent.external_delivery_enabled` (проставляется на emit из `settings.OUTBOX_EXTERNAL_DELIVERY_TOPICS`) и фильтром паблишера. Пустой список ⇒ ни одна строка не уходит наружу.

### 1.1 Create

| Пункт | Факт |
|---|---|
| Event name | `booking.created` |
| Producer | `appointments/application/services/create_booking_service.py:425` (`CreateBookingService._execute_atomic`) |
| Schema version | `1` (`envelope.py:60`) |
| Aggregate ID | `data.appointment_id` = `Appointment.id` |
| `tenant_id` (конверт) | `safe_tenant_id(appointment)` = `appointment.tenant_id`, зеркалит `specialist.tenant_id`. **Может быть `None`** — FK nullable, `safe_tenant_id` возвращает `None` с WARNING, не падает (`envelope.py:84-109`) |
| `user_id` (конверт) | `dto.client_id` — клиент. Корректный субъект |
| `correlation_id` | не передаётся ⇒ генерится свежий UUID на каждое событие |
| `data` | `appointment_id, client_id, specialist_id, service_id, start_at, end_at, status, source, price_total, payment_id, amount, specialist_timezone` |
| Одно ли событие | **НЕТ.** При `dto.confirm_immediately` (провайдерский walk-in, #1017) в той же транзакции эмитится второе — `booking.confirmed` (`create_booking_service.py:465-467`) |
| `appointment.created` существует? | **Нет.** Ни в `OutboxEvent.Topic`, ни в `EVENT_VERSIONS` |
| Почему `booking.created` | Историческое имя. Агрегат один — `Appointment`; модели `Booking` в Ayla нет (см. §4) |

### 1.2 Reschedule

| Пункт | Факт |
|---|---|
| Event names | **ДВА, оба на каждый reschedule** |
| Canonical | `appointment.rescheduled` — `cancel_reschedule_service.py:461`, топик `OutboxEvent.Topic.APPOINTMENT_RESCHEDULED` (`models.py:536`, «Запись перенесена (canonical)») |
| Legacy | `booking.rescheduled` — `cancel_reschedule_service.py:497`, топик `BOOKING_RESCHEDULED` (`models.py:529`) |
| Оба ли эмитятся всегда | **Да, безусловно, в одной транзакции.** Комментарий в коде (`:492-496`): «Legacy alias — UNCHANGED shape from pre-Wave-1. Kept so the not-yet-migrated bot consumer keeps working; do not remove until the bot-side wave migrates» |
| Общий correlation_id | **Да** — оба получают `correlation_id=command_correlation_id` |
| version / previous_version | `appointment.version + 1` / `new_version - 1`. Инкремент в `:393-399`, ревизия `AppointmentRevision` в `:401-412` |
| Порядок строк outbox | canonical пишется первым, legacy вторым; паблишер сортирует `order_by("created_at")` (`publisher.py:414`) ⇒ порядок доставки = canonical → legacy |
| Официальный для новых consumers | `appointment.rescheduled`. Payload нормативно задан «Ayla Domain Event Registry» v0.4 §6.3, решение AYLA-DEC-0022 п.9 |
| `data` (canonical) | required: `appointment_id, version, previous_version, revision_id, changed_fields, actor`; optional: `starts_at, previous_starts_at`; extras (не читаются никем): `specialist_id, client_id, old_end_at, new_end_at, basis, command_key` |
| `data` (legacy) | `appointment_id, specialist_id, client_id, start_at, end_at, new_start_at, old_start_at, rescheduled_by` |
| Расхождение `actor` | В canonical payload `actor` ∈ {`user`,`specialist`,`system`} (registry_actor), а `actor` **конверта** ∈ {`user`,`admin`,`system`}. Для specialist-инициированного переноса это буквально разные значения в одном сообщении. Так задумано (`:428-442`), но потребителю легко перепутать |

### 1.3 Cancel

| Пункт | Факт |
|---|---|
| Event name | `booking.cancelled` |
| Producers | три места: `cancel_reschedule_service.py:185` (`CancelBookingService`), `appointments/views.py:606` (ветка no-show), `users/services.py` → каскад «уход мастера» вызывает тот же `CancelBookingService` |
| `appointment.cancelled` существует? | **Нет** |
| Почему `booking.cancelled` | То же историческое именование, см. §4 |
| Schema version | `1` |
| Terminal status | `BookingStatus.CANCELLED` (`value_objects.py:84`), терминальный — из него переходов нет (`:104`) |
| `data` | `appointment_id, specialist_id, start_at, cancelled_by, reason_code, cancelled_at, initiator_role, refund_percent, reason` |
| `user_id` (конверт) | **`initiator_user_id` — инициатор, НЕ клиент записи.** При отмене мастером/системой это UUID мастера или админа. В каскаде ухода мастера — `actor.pk` либо `specialist_user.pk` |
| Одно ли событие | Из `CancelBookingService` — да, одно. **Но** путь no-show в `views.py` эмитит сначала `booking.no_show` (`:582`), затем `booking.cancelled` (`:606`) — это другая операция, но она порождает событие из пилотного набора |

---

## 2. BOT Consumer Matrix

Регистрация: `apps/eventbus/consumers/booking.py:1096` `register_booking_handlers()`, вызывается из `EventBusConfig.ready()`.

| Event | Handler | Version | Обяз. поля payload | tenant verification | Мутация |
|---|---|---|---|---|---|
| `booking.created` | `handle_booking_created` (`:264`) | 1 | `appointment_id`, `start_at`, `end_at`, `status` (жёсткое `data[...]`); `service_id`/`specialist_id`/`source` — опциональны | `assert_envelope_tenant_authorized(envelope)` первой строкой (`:274`) | `RemoteBookingProxy.get_or_create` по `appointment_id` + reminders + `Conversation.last_booking_at` |
| `booking.cancelled` | `handle_booking_cancelled` (`:366`) | 1 | `appointment_id`; `cancelled_by`/`reason_code` — опциональны | `:374` | `RemoteBookingProxy.status = CANCELLED`, отмена PENDING-reminders |
| `appointment.rescheduled` | `handle_appointment_rescheduled_canonical` (`:684`) | 1 | `appointment_id, version, previous_version, revision_id, changed_fields, actor`; `starts_at`/`previous_starts_at` — опциональны (`_CANONICAL_REQUIRED_FIELDS:586`) | внутри handler | `select_for_update` на proxy → сдвиг `start_at`/`end_at` с сохранением длительности + `last_applied_appointment_version` + перенос reminders |
| `booking.rescheduled` | `handle_booking_rescheduled` (`:438`) | 1 | `appointment_id`, `new_start_at` | `:445` | то же, но без version-ordering |

**Dedupe:** `IngestDedupe` по `event_id`, PK-коллизия ⇒ `DUPLICATE` → 200, handler не запускается (`ingest_dispatcher.py:254-265`). Строка dedupe пишется в одной транзакции с side-effect: исключение в handler откатывает и её, поэтому retry того же `event_id` реально повторяет обработку.

**Дополнительная идемпотентность:** `proxy.last_synced_event_id == envelope.event_id` ⇒ ранний выход; для canonical — сравнение `version` с `last_applied_appointment_version` (дубликат/устаревшая версия ⇒ no-op, разрыв версий ⇒ `CanonicalRescheduleVersionGapError`).

---

## 3. Schema Comparison

### 3.1 Create — расхождение по вокабуляру `status` ❌

Контракт `docs/architecture/event-contract.md:139`: `status` ∈ {`confirmed`, `pending_payment`, `tentative`}.
BOT `RemoteBookingProxy.Status` (`apps/booking/models.py:774-784`): `confirmed`, `pending_payment`, `tentative`, `cancelled`, `completed`, `no_show`.
Ayla шлёт `str(appointment.status)` ∈ {`awaiting_payment`, `confirmed`} (`create_booking_service.py:364-368`, `value_objects.py:80-85`).

**`awaiting_payment` не входит ни в контрактный enum, ни в enum BOT.** Это дефолтный путь обычного клиента (walk-in даёт `confirmed`).

Что произойдёт: BOT пишет `data["status"]` в поле дословно (`booking.py:299`), поле — `CharField(max_length=20, choices=...)`; Django **не валидирует choices** на `.create()`/`.update()`, строка (16 символов) помещается. Ошибки не будет — в зеркало запишется значение вне вокабуляра. Тихая порча данных, не отказ.

Смежное: `source` может прийти как `walk_in` (`create_booking_service.py:335-338`), а enum BOT — {`mobile_app`,`admin_console`,`automation`,`yclients_sync`}. Тот же класс, но §11 контракта прямо разрешает открытые enum'ы для `source` — здесь это допустимо, а для `status` нет.

### 3.2 Reschedule canonical — совпадает ✅

Поле в поле: `appointment_id, version, previous_version, revision_id, changed_fields, actor` + опциональные `starts_at, previous_starts_at`. Producer шлёт ровно это плюс extras; BOT extras игнорирует. Типы совпадают, `previous_version=0` парсится корректно (проверка `data.get(f) in (None, "")`, ноль её не триггерит).

### 3.3 Cancel — совпадает ✅

BOT требует только `appointment_id`, читает `cancelled_by`/`reason_code` через `.get()`. Producer шлёт оба со значениями из закрытых наборов §3.2, причём `reason_code` намеренно **не** выводится из свободного текста API (`_resolve_cancellation_vocab`, `:54-69`) — клиент не может подделать атрибуцию.

### 3.4 `tenant_id: null` — общий риск всех трёх ⚠️

`safe_tenant_id` возвращает `None` для незабэкфилленных строк. Все три события — tenant-scoped, и BOT отвергает `tenant_id: null` для них **на этапе парсинга конверта**: `ingest_envelope.py:225-226` → `invalid_tenant_id` → **HTTP 400** → у паблишера 4xx = немедленный dead-letter без ретраев. Одна строка `Appointment` с `tenant=NULL` в пилотном салоне = потерянное событие без шанса на повтор.

---

## 4. Booking vs Appointment Taxonomy

**Вердикт: вариант 2 — переходная event taxonomy.** Не два агрегата и не просто legacy-долг: миграция объявлена, канон зафиксирован решением, обе стороны знают целевое имя.

Обоснование только по коду и решениям:

1. **Агрегат один.** В `appointments/models.py` есть `Appointment` (`:20`) и `AppointmentRevision` (`:328`). Модели `Booking` не существует ни в одном приложении Ayla. Значит `booking.*` — префикс имени события над агрегатом `Appointment`, а не граница между агрегатами. Вопрос «в какой момент Booking превращается в Appointment» не имеет ответа, потому что превращения нет.
2. **Канон объявлен явно.** `OutboxEvent.Topic.APPOINTMENT_RESCHEDULED` подписан «Запись перенесена (canonical)» (`models.py:536`); payload нормативно задан Ayla Domain Event Registry v0.4 §6.3, зарегистрирован решением AYLA-DEC-0022 п.9.
3. **Legacy объявлен временным с условием снятия.** Комментарий у второго emit: «do not remove until the bot-side wave (04_AGENT_BOT_IMPLEMENTATION.md) migrates to the canonical topic above».
4. **BOT говорит то же самое с другой стороны.** `ingest_dispatcher.py:163-168`: «`appointment.rescheduled` is the canonical cross-repo event; `booking.rescheduled` remains a temporary repo-local legacy compatibility name».
5. **Пока мигрировала одна операция из трёх.** Canonical есть только у reschedule. Для create и cancel канонических `appointment.created`/`appointment.cancelled` не существует — миграция таксономии начата, но не завершена.

**Целевая каноническая схема:** `appointment.*` для всего жизненного цикла записи. Сегодня достигнуто на 1/3. Для create и cancel `booking.created` / `booking.cancelled` — **де-факто канон на пилот**, потому что альтернативы не существует; переименование post-pilot.

---

## 5. Canonical and Legacy Status

| Event | Статус | Комментарий |
|---|---|---|
| `booking.created` | canonical de facto | канонического имени не существует; переименование post-pilot |
| `booking.cancelled` | canonical de facto | то же |
| `appointment.rescheduled` | **canonical** | AYLA-DEC-0022 п.9, DER v0.4 §6.3 |
| `booking.rescheduled` | **legacy** | эмитится всегда, снимается только после миграции BOT |
| `booking.confirmed` | canonical de facto | вне пилотного набора, но эмитится на walk-in и на capture платежа |

---

## 6. Retry and DLQ Consequences

### 6.1 Расхождение спецификации и реализации ⚠️

`event-contract.md` §6.3: «Max attempts: 5 … backoff 1s, 5s, 30s, 120s, 600s», §6.4: «After 5 failed attempts … alert fires (PagerDuty rotation `ayla-events`)».
`publisher.py:99-107`: `MAX_DELIVERY_ATTEMPTS = 8`, backoff `2^attempt * 30s` с потолком 1 час, суммарно **≈4.5 часа** до dead-letter, dead-letter логируется как `logger.warning("outbox.publisher.dead_lettered …")` (`:425`).

Документ и код расходятся и по числу попыток, и по кривой, и по тому, что именно поднимает алерт. До flip'а это надо свести — иначе runbook дежурного описывает не ту систему.

### 6.2 Что даёт 4xx (немедленный dead-letter, без ретраев)

Паблишер: любой 4xx кроме 429 ⇒ `bot_delivery_status='dead'` сразу (`publisher.py:33-36`).

| Причина | Код BOT | Где |
|---|---|---|
| `tenant_id: null` у tenant-scoped события | 400 `invalid_tenant_id` | `ingest_envelope.py:225` |
| `user_id` пустой/не строка | 400 `missing_field` | `:229` |
| `event_name` вне `ALLOWED_EVENT_NAMES` | 400 `invalid_event_name` | `:199` |
| `actor` вне {system,user,admin} | 400 `invalid_actor` | — |
| Битая HMAC-подпись | 401 | `views.py:193` |
| Имя известно, версия не зарегистрирована | **422** + DLQ | `views.py:279-291` |
| `event_id` длиннее 36 символов | 422 + DLQ | `views.py:294-311` |

### 6.3 Что даёт 5xx (retryable, 8 попыток, ~4.5 ч, затем dead)

| Причина | Механизм |
|---|---|
| **Тенант или событие не в allowlist** | `TenantAuthorizationError` → `HANDLER_EXCEPTION` → **500** (`ingest_tenancy.py:558`, `views.py:361`) |
| Тенант в allowlist, но нет строки `Tenant` в БД BOT | то же, reason `tenant_not_found` |
| Отсутствует обязательное поле payload (`KeyError` в handler) | `HANDLER_EXCEPTION` → 500 |
| canonical reschedule пришёл раньше `booking.created` | `CanonicalReschedulePendingProxyError` → 500 (намеренно: тихий ack потерял бы перенос) |
| Разрыв версий canonical | `CanonicalRescheduleVersionGapError` → 500 |
| Перегрузка ingest | 503 + `Retry-After` |
| Rate limit | 429 — паблишер трактует как транзиентный |

**Ключевой вывод:** ошибки конфигурации (allowlist) ретраятся 4.5 часа и умирают; ошибки схемы умирают мгновенно. Для дежурного это два разных сценария с разной срочностью, и ни один из них сейчас не описан в runbook в этих терминах.

### 6.4 Безопасно ли включать топик при частично несовпадающем контракте

**Нет.** Но важная поправка к моему прежнему отчёту: катастрофа «11 остальных tenant-scoped имён запейджат» **не наступает автоматически**, потому что внешняя доставка на стороне Ayla гейтится тем же per-topic allowlist'ом `OUTBOX_EXTERNAL_DELIVERY_TOPICS`. Если Ayla включит ровно три топика, остальные строки просто не уйдут наружу — 500 не будет.

Реальный операционный риск другой и уже, но он существует:

* **Включение `booking.rescheduled` вместе с `appointment.rescheduled`.** Это самая вероятная ошибка оператора, потому что legacy-топик всё ещё живой и обслуживает внутренние обработчики. BOT его отвергнет (нет в allowlist) ⇒ **на каждый перенос — 8 попыток за 4.5 ч и dead-letter**. Внутренняя доставка legacy-события при этом не пострадает: она не зависит от `external_delivery_enabled`.
* **`tenant=NULL` в пилотных строках.** 400, мгновенная смерть события, без ретраев.

---

## 7. Approved Pilot Topic List

Разрешать flip можно только там, где: producer подтверждён, consumer подтверждён, schema совпадает, version совпадает, canonical/legacy статус понятен, двойная доставка исключена.

| Operation | Topic | Version | Canonical status | Enable for Pilot |
|---|---|---|---|---|
| Create | `booking.created` | 1 | canonical de facto | **NO — ACK AFTER FIX** |
| Reschedule | `appointment.rescheduled` | 1 | canonical (AYLA-DEC-0022) | **NO — ACK AFTER FIX** |
| Cancel | `booking.cancelled` | 1 | canonical de facto | **YES — условно** |

**Cancel** — единственная операция, проходящая все шесть критериев: один producer на операцию, схема совпадает, вокабуляр `cancelled_by`/`reason_code` закрыт и защищён от подделки, двойной доставки нет. Условия: (а) в `OUTBOX_EXTERNAL_DELIVERY_TOPICS` не попадает `booking.no_show`, иначе путь no-show даст парную доставку с отказом по одной из них; (б) у пилотных записей `tenant_id` не NULL.

**Create** блокируется расхождением вокабуляра `status` (§3.1) — зеркало будет писать значение вне контракта, и без `booking.confirmed` никогда не перейдёт в `confirmed`.

**Reschedule** блокируется двойной эмиссией: если в allowlist Ayla попадут оба имени, каждый перенос будет генерировать dead-letter.

---

## 8. Required Fixes

Порядок — по убыванию блокирующей силы.

**F-1 · BLOCKER · Ayla · вокабуляр `status` в `booking.created`.**
Producer шлёт `awaiting_payment`; контракт §3.1 и enum BOT знают `pending_payment`. Нужно решение: либо маппинг на стороне Ayla при формировании payload, либо расширение контракта + enum BOT. Пока не сведено — зеркало BOT содержит статус вне вокабуляра на **каждой** обычной клиентской записи.

**F-2 · BLOCKER · процесс · только canonical reschedule в allowlist Ayla.**
`OUTBOX_EXTERNAL_DELIVERY_TOPICS` должен содержать `appointment.rescheduled` и **не** содержать `booking.rescheduled`. Требуется письменное подтверждение точного содержимого переменной перед flip, а не устная договорённость.

**F-3 · BLOCKER · Ayla · backfill `Appointment.tenant_id` для пилотного салона.**
Любая строка с `tenant=NULL` даёт 400 и мгновенный dead-letter. Нужен запрос-подтверждение `SELECT count(*) FROM appointments_appointment WHERE tenant_id IS NULL` = 0 в пределах пилотного тенанта.

**F-4 · PRE_PILOT · решение по `booking.confirmed`.**
Без него зеркало BOT остаётся в `awaiting_payment` навсегда, а `_schedule_reminders` вызывается на `booking.created` **безусловно, без проверки статуса** (`booking.py:334-340`) — то есть напоминания уйдут и по неоплаченной записи. Варианты: добавить четвёртое имя в пилотный набор, либо принять и задокументировать, что зеркало не отражает подтверждение.

**F-5 · PRE_PILOT · порядок доставки canonical reschedule.**
Перенос записи, созданной **до** flip'а, не найдёт proxy → `CanonicalReschedulePendingProxyError` → 8 ретраев → dead. Нужно либо принять окно (переносы старых записей теряются), либо предзагрузить зеркало, либо включать reschedule позже create с запасом.

**F-6 · FOLLOW_UP · свести §6.3/§6.4 контракта с `publisher.py`.**
Документ: 5 попыток, кривая 1s→600s, PagerDuty. Код: 8 попыток, `2^n*30s` до 1 ч, ~4.5 ч, `logger.warning`. Runbook дежурного сейчас описывает не ту систему.

**F-7 · FOLLOW_UP · задокументировать семантику `user_id` в `booking.cancelled`.**
Это **инициатор**, не клиент записи. Любой будущий consumer, который примет `envelope.user_id` за субъект брони, будет неправ. После #246 проверка TUR тоже пойдёт по инициатору.

**F-8 · NICE · `actor` в canonical reschedule.**
В конверте `admin`, в payload `specialist` — для одного и того же действия. Задумано, но требует явной строки в реестре, иначе это ловушка.

---

## 9. Final ACK

### **ACK AFTER FIX**

Включать топики в текущем виде нельзя. Не из-за пейджинг-катастрофы — двойной гейт на стороне Ayla её предотвращает — а потому что два из трёх контрактов не сходятся по существу:

* **Create** пишет в зеркало статус вне вокабуляра на каждой обычной записи (F-1).
* **Reschedule** эмитит два события на одну операцию, и цена ошибки оператора в одной переменной окружения — dead-letter на каждый перенос (F-2).
* **Cancel** контрактно чист и готов.

Минимальный набор для перехода в **ACK SAFE TO FLIP**: закрыть F-1, F-2, F-3 и принять решение по F-4.

Порядок включения при выполненных условиях — по одному топику с паузой на сверку строк аудита `eventbus.ingest.tenant_verify_accepted`, а не все три разом:

1. `booking.cancelled` — контрактно готов уже сейчас;
2. `booking.created` — после F-1 и F-3;
3. `appointment.rescheduled` — последним, после F-2 и F-5, т.к. его handler зависит от наличия proxy, созданного шагом 2.

Rollback (очистка переменных) обладает тем же свойством 500 → ретраи → dead, поэтому согласовывается с дежурным Ayla, а не делается односторонне.

---

**Что дальше:** остановка после отчёта. PR-T02-2 не начат. Deploy не выполнялся, флаги не менялись.
