# Provider Booking Detail Flow

| Field | Value |
|---|---|
| **Date** | 2026-05-29 r2 (canonicalized by Tau) |
| **Designer** | Codex draft → Tau canonical consolidation |
| **Status** | Canonical — consolidated for Tech Lead review; updated for Ayla auto-reschedule UX |
| **Surface** | Ayla Pro Mini App |
| **Audience** | Provider-side: solo-master, team-master, salon admin, salon owner |
| **Primary persona** | Ольга — self-employed master, one BotUser = owner + admin + master |
| **Secondary personas** | Team-master, salon administrator, salon owner |
| **Phase** | P0 PRE_PILOT — provider-side bridge for customer booking lifecycle |
| **Channel** | Ayla Pro Mini App + manager-bot DM push |
| **Scope** | Provider-side booking detail screen: booking status, customer/service recap, client note, Ayla-mediated messages, Ayla auto-reschedule notification, role-based actions, timeline |
| **Screens** | 1 main detail screen + 6 state screens + 4 confirmation/error modals |
| **Related customer flows** | `customer-booking-flow.md`, `customer-cancellation-reschedule-flow.md`, `customer-records-flow.md` |
| **Related provider docs** | `master-solo-surface.md`, `solo-provider-ux.md`, `ayla-mediated-messaging.md`, `design-tokens.md` |
| **Severity** | P0 BLOCKER for real provider operations |

---

## Foundation references

| Doc | Why it matters |
|---|---|
| `customer-booking-flow.md` | Customer creates booking; this screen is the provider-side mirror of that lifecycle |
| `customer-cancellation-reschedule-flow.md` | Cancellation/reschedule/no-fault states must render correctly provider-side |
| `customer-records-flow.md` | Customer sees booking history; provider must see operational booking state |
| `master-solo-surface.md` | Solo-provider is first-class; Ольга combines owner/admin/master responsibilities |
| `solo-provider-ux.md` | Defines universal solo-provider behavior and permission assumptions |
| `ayla-mediated-messaging.md` | Customer-provider communication is mediated by Ayla, not raw direct chat |
| `design-tokens.md` | Visual source of truth: sage-green, Manrope, Lucide icons, 12dp cards, low-shadow UI |
| `schedule-management-handoff.md` | Booking detail links to schedule/calendar actions but does not replace schedule module |
| `master-mobile-handoff.md` | Team-master visibility constraints and mobile operational context |

---

## 0. Overview

### What this module is

`Provider Booking Detail` is the provider-side detail screen for a single booking.

Customer-side flows already let the customer:

1. find a service;
2. choose master/salon;
3. choose date and time;
4. confirm booking;
5. see booking in records;
6. cancel or reschedule.

This screen answers the provider-side question:

> “A customer booked. What exactly is happening, what do I need to know, and what can I do now?”

This is the operational bridge between customer booking and real salon/master work.

---

### Why this matters

Without provider booking detail:

- customer booking exists, but master/salon may not understand what happened;
- messages like “I’m late” have no clear destination;
- payment failure may be missed;
- cancelled/rescheduled bookings may still look actionable;
- Ayla can reschedule eligible bookings automatically, but provider still needs a clear post-action notification and timeline event;
- solo-master cannot safely manage their own booking;
- team-master may get too much access or too little access;
- admin has no clean screen for cancellation, reschedule, no-fault and provider-side intervention.

This screen is the minimum provider-side operating unit for pilot.

---

### Product promise

Provider Booking Detail should let provider understand the booking in under 10 seconds:

- who;
- when;
- what service;
- what status;
- what message/note;
- what actions are allowed.

It must be calm, role-aware, privacy-safe and Ayla-mediated.

---

## 1. Personas

### 1.1. Ольга — solo-master

Ольга is one person with three responsibilities:

- owner;
- admin;
- master.

She manages her own day, services, clients, schedule, booking actions and customer communication.

For Ольга, this screen should feel like:

> “Вот моя запись. Всё понятно. Я могу спокойно обслужить клиента или изменить запись, если нужно.”

---

### 1.2. Team-master

Team-master works inside a salon.

They need enough information to perform the service, but not salon-level financial, administrative or sensitive customer data.

Team-master sees:

- their own booking;
- customer display name;
- service;
- time;
- duration;
- customer note if relevant;
- Ayla-mediated message if assigned to them.

Team-master should not see by default:

- full customer phone;
- detailed payment data;
- salon revenue;
- other masters’ bookings;
- internal admin-only fields;
- customer wellness memory.

---

### 1.3. Salon administrator

Admin operates daily booking flow.

Admin sees:

- all bookings in salon;
- master assignment;
- customer contact controls;
- payment operational status;
- cancellation/reschedule actions;
- no-fault flow;
- customer messages by booking.

Admin owns operational resolution.

---

### 1.4. Salon owner

Owner sees what admin sees, plus can move to financial/analytics screens.

This screen should not become an analytics dashboard. Owner-level financial deep dive is out of scope here.

---

## 2. User jobs

### Solo-master JTBD

> “Когда я открываю запись, я хочу сразу понять кто придёт, на какую услугу и что нужно сделать — чтобы спокойно подготовиться и не держать всё в голове.”

### Team-master JTBD

> “Когда у меня следующая запись, я хочу видеть только нужную информацию по клиенту и услуге — чтобы не отвлекаться на админские детали.”

### Admin JTBD

> “Когда с записью что-то произошло — перенос, отмена, клиент написал, оплата не подтверждена — я хочу быстро решить ситуацию без хаоса.”

### Owner JTBD

> “Когда я проверяю запись, я хочу видеть, что процесс под контролем, а доступы и действия распределены правильно.”

---

## 3. Entry points

### E1. From “Мой день”

Provider taps appointment card on dashboard.

Example:

```text
10:00 · Анна С.
Массаж спины · 60 мин
```

---

### E2. From “Записи” / calendar

Provider opens day/week calendar and selects a booking.

---

### E3. From manager-bot DM

Manager-bot push opens booking detail.

Push triggers:

- new booking;
- customer cancelled;
- customer rescheduled;
- Ayla auto-rescheduled after customer selected a new slot;
- customer wrote by booking;
- payment not confirmed;
- master unavailable/no-fault;
- booking requires admin action.

---

### E4. From messages

If customer used “Сообщить по записи”, provider opens this booking detail with message block highlighted.

If the customer asked to reschedule and Ayla successfully completed the Booking Engine workflow, provider opens this booking detail with the reschedule timeline event highlighted instead of a raw chat thread.

---

### E5. From customer card

Admin/solo-master opens customer profile and taps a booking from history.

---

## 4. Main screen structure

Provider Booking Detail has 8 blocks.

```text
B1 — Status header
B2 — Main booking recap
B3 — Client block
B4 — Service details
B5 — Client note
B6 — Messages by booking
B7 — Operational actions
B8 — Timeline
```

---

## 5. B1 — Status header

### Purpose

Immediately shows whether booking is normal, needs attention, finished, cancelled or blocked.

### Status labels

| Backend status | UI label | Tone |
|---|---|---|
| `pending` | Ожидает подтверждения | calm |
| `confirmed` | Подтверждена | calm |
| `customer_message` | Клиент написал | attention |
| `rescheduled` | Перенесена | neutral |
| `rescheduled_by_ayla` | Перенесена через Ayla | neutral |
| `customer_cancelled` | Отменена клиентом | neutral |
| `provider_cancelled` | Отменена салоном | neutral |
| `payment_failed` | Оплата не подтверждена | non-blaming |
| `completed` | Завершена | calm |
| `no_show` | Не состоялась | non-blaming |
| `no_fault` | Требует переноса | calm attention |

### Copy rules

Use:

```text
Подтверждена
Клиент написал
Оплата не подтверждена
Не состоялась
Требует переноса
```

Do not use:

```text
Клиент не оплатил
Клиент не пришёл
Просрочено
Проблема!
Ошибка клиента
```

### Visual rules

- Status must not rely on color only.
- Use label + icon + optional helper text.
- Avoid aggressive red unless operationally critical.
- For `payment_failed`, use calm warning state, not danger state.

---

## 6. B2 — Main booking recap

### Purpose

Core booking facts.

### Fields

| Field | Example |
|---|---|
| Date | Завтра |
| Time | 16:00 |
| Duration | 60 минут |
| Service | Массаж спины |
| Master | Ольга |
| Salon/place | Эстетика тела |
| Price | 2 500 ₽ |
| Payment mode | Оплата на месте / Оплата не подтверждена |

### Example layout

```text
Завтра, 16:00
Массаж спины · 60 минут

Мастер: Ольга
Салон: Эстетика тела
Стоимость: 2 500 ₽
```

### Notes

- Price can be hidden from team-master depending on role policy.
- Payment detail should be operational, not financial analytics.
- If booking changed, show current state as source of truth.

---

## 7. B3 — Client block

### Purpose

Shows who is coming and what provider is allowed to know.

### Fields

| Field | Solo-master | Team-master | Admin/Owner |
|---|---:|---:|---:|
| First name | yes | yes | yes |
| Last initial | yes | yes | yes |
| Full name | optional | no by default | yes if allowed |
| Phone | masked by default | hidden/masked | masked/visible by permission |
| Visits count | yes | optional | yes |
| Last visit | yes | optional | yes |
| Customer card link | yes | limited | yes |

### Default client display

```text
Анна С.
3 визита
Последний визит: 20 июня
```

### Phone visibility locked decision

Phone is masked by default.

```text
+7 ••• •••-12-34
[Показать номер]
```

Reveal requires permission and audit.

### Reveal modal

```text
Показать номер клиента?

Используй номер только по этой записи. Это действие будет сохранено в журнале.

[Показать]
[Отмена]
```

### Communication hierarchy

Primary communication remains:

```text
Ответить через Ayla
```

Phone is backup, not primary channel.

---

## 8. B4 — Service details

### Purpose

Clarifies what exactly the provider is expected to do.

### Fields

- service name;
- duration;
- category;
- price;
- preparation notes;
- salon-approved contraindication text if exists;
- what is included.

### Copy safety

Allowed:

```text
Подготовка: прийти за 5 минут до начала.
В услугу входит: работа со спиной и шеей.
```

Not allowed:

```text
Эта процедура лечит защемление.
Поможет при грыже.
Гарантирует результат.
```

Service detail can show salon-provided text, but must not generate medical claims.

---

## 9. B5 — Client note

### Purpose

Shows note entered by customer.

### Example

```text
Заметка клиента:
“Хочу мягко, болит поясница.”
```

### Rules

- Show note as customer’s own words.
- Do not reinterpret note medically.
- Do not surface customer wellness memory here.
- If no note, hide block entirely.

### Label

Use:

```text
Заметка клиента
```

Do not use:

```text
Диагноз
Медицинская информация
Проблема клиента
```

---

## 10. B6 — Messages by booking

### Purpose

Shows communication linked to this booking.

This is not direct chat. This is Ayla-mediated messaging.

### Important distinction

A customer reschedule request is not just a message.

If Ayla can resolve it through the official Booking Engine workflow, it becomes a booking lifecycle event:

```text
customer asks to reschedule
→ Ayla checks policy and available slots
→ customer confirms new time
→ backend atomically reschedules booking
→ provider receives post-action notification
```

Provider should not receive a raw “please approve this normal reschedule” message when all rules pass.

Provider should receive:

```text
Запись перенесена через Ayla
Было: сегодня 16:00
Стало: сегодня 18:00
```

If rules fail, the request becomes a provider/admin message or task.

---

### Default state

If no messages:

```text
Сообщений по этой записи пока нет.
```

Can be hidden if screen is dense.

### New message state

```text
Клиент написал:
“Я задержусь на 10 минут.”
```

Actions:

```text
[Ответить через Ayla]
[Отметить как решено]
```

### Allowed CTAs

- Ответить через Ayla
- Открыть сообщения
- Отметить как решено
- Передать администратору

### Forbidden CTAs

- Чат с клиентом
- Написать напрямую
- Позвонить клиенту immediately as primary action
- Ответить от имени мастера клиенту напрямую

### Reason

Customer should experience Ayla as the stable communication layer. Provider internally may compose response, but customer-facing message goes through Ayla-mediated channel.

---

## 11. B7 — Operational actions

Actions depend on role and status.

### Core actions

| Action | Solo-master | Team-master | Admin | Owner |
|---|---:|---:|---:|---:|
| Confirm booking | yes | limited | yes | yes |
| Reschedule | yes | request/admin only | yes | yes |
| Cancel | yes with confirmation | no direct | yes | yes |
| Request admin action | optional | yes | n/a | n/a |
| Reply via Ayla | yes | yes if assigned | yes | yes |
| Mark arrived | yes | yes | yes | yes |
| Mark completed | yes | yes | yes | yes |
| Open client | yes | limited | yes | yes |
| Create repeat booking | yes | optional | yes | yes |
| Reassign master | no if solo | no | yes | yes |

---

## 12. B8 — Timeline

### Purpose

Shows short operational history.

### Example

```text
Создана сегодня в 12:34
Клиент подтвердил в 12:36
Ayla перенесла запись: 16:00 → 18:00
Напоминание отправится перед визитом
```

### Timeline events

- booking created;
- confirmed;
- rescheduled;
- rescheduled by Ayla after customer confirmed new slot;
- customer wrote;
- provider replied;
- cancelled;
- completed;
- payment status changed.

### Scope

MVP shows compact human-readable timeline.

Full audit log is out of scope.

---

## 13. Locked decisions

### Q-PBD-1 — Solo-master cancellation

**Decision:** LOCKED.

Solo-master can cancel own booking, but cancellation always requires confirmation.

For near-term bookings, paid bookings or same-day bookings, require reason and suggest reschedule/no-fault alternative.

#### UX rule

Normal cancellation:

```text
Отменить запись?
Анна С. · Массаж спины · завтра 16:00

Клиент получит уведомление.

[Отменить запись]
[Назад]
```

Near-term cancellation:

```text
Запись скоро начнётся

Лучше предложить перенос, чтобы клиент не потерял визит.

Причина:
[________________]

[Предложить перенос]
[Всё равно отменить]
[Назад]
```

---

### Q-PBD-2 — Team-master cancellation

**Decision:** LOCKED.

Team-master cannot directly cancel booking by default in MVP.

Team-master can request admin action.

#### UX rule

Team-master sees:

```text
[Попросить перенос]
[Попросить отмену]
[Сообщить администратору]
```

Admin receives actionable request.

Team-master does not see direct destructive action unless salon policy explicitly enables it post-MVP.

---

### Q-PBD-3 — Customer phone visibility

**Decision:** LOCKED.

Phone is masked by default.

Reveal depends on role and must be auditable.

Primary communication remains Ayla-mediated.

#### Role defaults

| Role | Phone default |
|---|---|
| Solo-master | masked, click-to-reveal |
| Team-master | hidden or masked, salon-policy dependent |
| Admin | masked/visible by permission |
| Owner | masked/visible by permission |

#### Audit event

Every reveal should create audit event:

```text
provider.customer_phone.revealed
```

Payload:

```json
{
  "booking_id": "uuid",
  "customer_id": "uuid",
  "actor_id": "uuid",
  "actor_role": "admin",
  "reason": "booking_operational_need"
}
```

---

### Q-PBD-4 — Payment visibility for team-master

**Decision:** LOCKED.

Team-master sees operational payment label only.

Detailed payment data is visible to solo-master/admin/owner if permission allows.

#### Good labels

```text
Оплата на месте
Оплата подтверждена
Оплата не подтверждена
Попроси администратора проверить
```

#### Bad labels

```text
Клиент не оплатил
Долг клиента
Ошибка карты клиента
Платёж отклонён банком
```

---

### Q-PBD-5 — Mark completed and B11 review prompt

**Decision:** LOCKED.

“Mark completed” does not directly send review prompt.

It changes booking status to completed. Backend then evaluates B11 eligibility.

#### Correct chain

```text
mark_completed
→ booking.status = completed
→ backend checks B11 eligibility
→ if allowed, schedules review prompt
```

#### Must respect blockers

- refund;
- payment dispute;
- no-fault;
- provider-cancelled;
- no-show;
- sensitive complaint;
- proactive opt-out;
- customer muted reminders/messages.

---

### Q-PBD-6 — No-show wording

**Decision:** LOCKED.

Use:

```text
Не состоялась
```

Do not use:

```text
Клиент не пришёл
Клиент не пришла
No-show customer
```

Reason, if needed, is separate and neutral.

Example:

```text
Визит не состоялся.
Причина: клиент предупредил, что не сможет прийти.
```

---

---

### Q-PBD-7 — Ayla auto-reschedule provider approval

**Decision:** LOCKED.

Provider approval is not required for a normal eligible customer reschedule when Ayla completes the official Booking Engine workflow.

Ayla may reschedule automatically only if:

```text
booking belongs to the customer
booking is active
reschedule policy allows it
available slot is fetched from backend
customer explicitly confirms new time
backend transaction succeeds atomically
billing chain checks pass
no admin lock / payment dispute / no-fault conflict exists
```

Provider receives a post-action notification, not an approval request.

Provider notification copy:

```text
Запись перенесена через Ayla

Анна С.
Массаж спины · 60 мин

Было:
Сегодня, 16:00

Стало:
Сегодня, 18:00

[Открыть запись]
```

If any rule fails, Ayla escalates to admin/provider instead of rescheduling automatically.

Do not show this as direct chat.

This is a booking lifecycle event.


## 14. State matrix

### S1. Loading

Show skeleton for:

- status header;
- recap card;
- client block;
- actions.

No blank screen.

---

### S2. Not found

```text
Запись не найдена.

Возможно, она была удалена или у тебя нет доступа.
```

CTA:

```text
[Вернуться назад]
[Открыть расписание]
```

---

### S3. Permission denied

```text
Нет доступа к этой записи.
```

CTA:

```text
[Вернуться]
[Сообщить администратору]
```

---

### S4. Offline

```text
Нет соединения.

Показываю последние сохранённые данные, если они доступны.
```

Rules:

- mutation actions disabled;
- read-only cached content allowed if available;
- show “обновится при подключении”.

---

### S5. Stale data

If booking changed while screen was open:

```text
Запись обновилась. Показываю актуальную версию.
```

Actions must update after refetch.

---

### S6. Integration error

```text
Не получилось обновить запись.

Попробуй ещё раз или передай администратору.
```

CTA:

```text
[Попробовать ещё раз]
[Сообщить администратору]
```

---

## 15. Edge cases

### 15.1. Customer cancelled while provider screen was open

Behavior:

- disable outdated actions;
- update status to “Отменена клиентом”;
- show toast or inline notice.

Copy:

```text
Клиент отменил запись. Данные обновлены.
```

---

### 15.2. Customer rescheduled from another flow

If customer completed reschedule through Ayla, show it as a lifecycle event.

Copy:

```text
Запись перенесена через Ayla.

Было: сегодня, 16:00.
Стало: завтра, 17:00.
```

Actions:

```text
[Открыть запись]
[Открыть в календаре]
```

Do not ask provider to approve a completed eligible reschedule.

---

### 15.3. Master unavailable / sick day

For solo-master:

- suggest reschedule;
- send no-fault customer message;
- do not expose raw reason to customer unless master explicitly chooses a neutral wording.

For salon:

- admin can reassign master;
- admin can offer alternate slot;
- admin can cancel with no-fault messaging.

Customer should not see:

```text
мастер заболел
мастер заблокирован
мастер не вышел
```

Customer sees neutral Ayla framing.

---

### 15.4. Payment not confirmed

Show:

```text
Оплата не подтверждена.
```

Do not blame customer.

Possible actions:

- Проверить статус;
- Сообщить администратору;
- Открыть оплату;
- Продолжить как “оплата на месте”, if salon policy allows.

---

### 15.5. Client says “I’m late”

Message block becomes important.

Copy:

```text
Клиент написал:
“Я задержусь на 10 минут.”
```

Actions:

```text
[Ответить через Ayla]
[Отметить как решено]
```

---

### 15.6. Booking already completed

Do not show destructive actions.

Show:

- completed status;
- service summary;
- customer card;
- repeat booking CTA;
- review status if available.

---

### 15.7. Booking is today and action is destructive

If provider tries to cancel/reschedule a near-term booking:

- require confirmation;
- ask for reason;
- suggest reschedule before cancellation;
- show that client will be notified.

---

## 16. Confirmation modals

### M1. Cancel booking

```text
Отменить запись?

Анна С. · Массаж спины · завтра 16:00

Клиент получит уведомление от Ayla.

[Отменить запись]
[Назад]
```

---

### M2. Near-term cancel

```text
Запись скоро начнётся

Лучше предложить перенос, чтобы клиент не потерял визит.

Причина отмены:
[________________]

[Предложить перенос]
[Всё равно отменить]
[Назад]
```

---

### M3. Mark completed

```text
Завершить визит?

После этого запись перейдёт в историю. Если всё прошло корректно, Ayla позже сможет попросить клиента оставить отзыв.

[Завершить визит]
[Назад]
```

Do not promise immediate review prompt.

---

### M4. Reveal phone

```text
Показать номер клиента?

Используй номер только по этой записи. Действие будет сохранено в журнал.

[Показать номер]
[Отмена]
```

---

## 17. Provider-side CTA vocabulary

### Approved CTA

```text
Открыть клиента
Ответить через Ayla
Перенести
Отменить
Подтвердить
Отметить приход
Завершить визит
Создать повторную запись
Передать администратору
Попросить перенос
Попросить отмену
Показать номер
```

### Forbidden CTA

```text
Чат с клиентом
Написать напрямую
Клиент не оплатил
Клиент не пришёл
Наказать
Просрочка
Проблема клиента
```

---

## 18. Role-based visibility matrix

| Block / action | Solo-master | Team-master | Admin | Owner |
|---|---:|---:|---:|---:|
| Booking recap | yes | yes | yes | yes |
| Client name | yes | limited | yes | yes |
| Client phone | masked + reveal | hidden/masked | masked/visible | masked/visible |
| Client visits count | yes | optional | yes | yes |
| Full customer card | yes | limited | yes | yes |
| Service details | yes | yes | yes | yes |
| Client note | yes | yes if assigned | yes | yes |
| Messages by booking | yes | yes if assigned | yes | yes |
| Payment operational label | yes | limited | yes | yes |
| Payment details | yes if solo-owner | no | yes | yes |
| Confirm | yes | limited | yes | yes |
| Reschedule | yes | request only | yes | yes |
| Cancel | yes with modal | request only | yes | yes |
| Reassign master | no if solo | no | yes | yes |
| Mark arrived | yes | yes | yes | yes |
| Mark completed | yes | yes | yes | yes |
| Timeline | yes | limited | yes | yes |
| Audit drawer | no | no | yes | yes |

---

## 19. API draft

### 19.1. GET booking detail

```text
GET /api/v1/provider/bookings/{booking_id}
```

### Response draft

```json
{
  "id": "uuid",
  "status": "confirmed",
  "status_label": "Подтверждена",
  "starts_at": "2026-07-15T16:00:00+03:00",
  "ends_at": "2026-07-15T17:00:00+03:00",
  "duration_minutes": 60,
  "service": {
    "id": "uuid",
    "name": "Массаж спины",
    "category": "massage",
    "price": 2500,
    "currency": "RUB"
  },
  "client": {
    "id": "uuid",
    "display_name": "Анна С.",
    "phone_masked": "+7 ••• •••-12-34",
    "phone_reveal_available": true,
    "visits_count": 3,
    "last_visit_at": "2026-06-20"
  },
  "provider": {
    "tenant_id": "uuid",
    "salon_name": "Эстетика тела",
    "master_id": "uuid",
    "master_name": "Ольга"
  },
  "client_note": {
    "text": "Хочу мягко, болит поясница",
    "created_at": "2026-07-01T12:40:00+03:00"
  },
  "message_summary": {
    "has_unread": true,
    "last_message": "Я задержусь на 10 минут",
    "last_message_at": "2026-07-15T15:40:00+03:00"
  },
  "payment": {
    "visibility": "operational_label",
    "status": "not_required",
    "label": "Оплата на месте"
  },
  "available_actions": [
    "reschedule",
    "cancel",
    "reply_via_ayla",
    "mark_arrived",
    "mark_completed"
  ],
  "timeline": [
    {
      "type": "created",
      "label": "Запись создана",
      "created_at": "2026-07-01T12:34:00+03:00"
    },
    {
      "type": "confirmed",
      "label": "Клиент подтвердил",
      "created_at": "2026-07-01T12:36:00+03:00"
    },
    {
      "type": "rescheduled_by_ayla",
      "label": "Ayla перенесла запись: 16:00 → 18:00",
      "created_at": "2026-07-15T12:10:00+03:00"
    }
  ]
}
```

---

### 19.2. Actions

```text
POST /api/v1/provider/bookings/{booking_id}/confirm
POST /api/v1/provider/bookings/{booking_id}/reschedule
POST /api/v1/provider/bookings/{booking_id}/cancel
POST /api/v1/provider/bookings/{booking_id}/mark-arrived
POST /api/v1/provider/bookings/{booking_id}/complete
POST /api/v1/provider/bookings/{booking_id}/reply-via-ayla
POST /api/v1/provider/bookings/{booking_id}/request-admin-action
POST /api/v1/provider/bookings/{booking_id}/reveal-phone
```

---

### 19.3. Cancel payload

```json
{
  "reason": "master_unavailable",
  "note": "Не смогу принять в это время",
  "offer_reschedule": true
}
```

---

### 19.4. Request admin action payload

```json
{
  "requested_action": "cancel",
  "reason": "Не смогу принять клиента в это время",
  "urgency": "same_day"
}
```

---

### 19.5. Reply via Ayla payload

```json
{
  "message": "Анна, спасибо, я учту задержку. Жду вас.",
  "source": "provider_composed",
  "booking_id": "uuid"
}
```

Customer-facing message is rendered through Ayla-mediated flow, not direct provider identity.

---

## 20. Events

### Required events

```text
provider.booking.detail.opened
provider.booking.confirmed
provider.booking.reschedule.started
provider.booking.rescheduled_by_ayla
provider.booking.cancel.started
provider.booking.cancel.confirmed
provider.booking.admin_action_requested
provider.booking.arrival_marked
provider.booking.completed
provider.booking.phone_revealed
provider.booking.reply_via_ayla.started
provider.booking.reply_via_ayla.sent
provider.booking.stale_refetched
provider.booking.permission_denied
```

### Important payload fields

```json
{
  "booking_id": "uuid",
  "tenant_id": "uuid",
  "actor_id": "uuid",
  "actor_role": "solo_master",
  "status_before": "confirmed",
  "status_after": "completed",
  "source_surface": "ayla_pro_mini_app"
}
```

---

## 21. Frontend implementation notes

### Visual style

Use design tokens:

- sage-green primary accent;
- warm neutral background;
- Manrope;
- Lucide icons;
- 12dp cards;
- 1dp hairline border;
- low shadow;
- calm status vocabulary.

### Layout order

Mobile-first:

```text
1. Status header
2. Booking recap
3. Client block
4. Message block if active
5. Client note if exists
6. Service details
7. Actions
8. Timeline
```

### Sticky action area

For active booking, primary action can be sticky bottom:

- “Ответить через Ayla” if unread message;
- “Отметить приход” before visit;
- “Завершить визит” after start.

Do not show too many sticky actions at once.

---

## 22. Accessibility

Minimum requirements:

- body text 16px equivalent;
- tap targets ≥ 44px;
- status labels not color-only;
- destructive actions require confirmation;
- focus visible;
- reduced motion respected;
- error messages readable and specific;
- icons always paired with text;
- no dense financial/detail tables for team-master mobile.

---

## 23. MVP scope

### In scope

- read booking detail;
- role-based visibility;
- status rendering;
- client block;
- service block;
- client note;
- message summary;
- reply via Ayla entry;
- basic actions;
- cancel/complete confirmation;
- phone masking + reveal;
- compact timeline;
- loading/error/offline states.

---

### Out of scope

- full audit log viewer;
- complex payment ledger;
- refund ledger;
- advanced CRM;
- attachment upload;
- call recording;
- internal master-admin chat;
- public review response;
- multi-booking batch actions;
- customer wellness memory;
- detailed analytics.

---

## 24. Acceptance criteria

1. Provider can open booking detail from “Мой день”, “Записи”, notification or message.
2. Screen shows booking status, date, time, service, duration, client and master.
3. Status label is calm and non-blaming.
4. Team-master does not see full financial/payment details.
5. Team-master cannot directly cancel by default.
6. Solo-master can cancel own booking with confirmation modal.
7. Same-day/near-term cancellation asks for reason and suggests reschedule/no-fault alternative.
8. Phone is masked by default.
9. Phone reveal is role-gated and audited.
10. Client note is shown as customer’s words and not medically interpreted.
11. Customer message appears in booking-linked message block.
12. Customer communication uses “Ответить через Ayla”, not direct chat wording.
13. Payment failure is shown as “Оплата не подтверждена”.
14. “Завершить визит” marks booking completed but does not directly send review prompt.
15. Backend B11 eligibility decides review prompt scheduling.
16. No-show status is “Не состоялась”.
17. Stale screen refetches and disables outdated actions.
18. Destructive actions require confirmation.
19. Offline mode disables mutation actions.
20. UI follows design tokens.
21. Role-based visibility is enforced server-side, not only in UI.
22. Permission denied state is covered.
23. Not found state is covered.
24. Events emitted for major actions.
25. Tests cover solo-master, team-master, admin and owner visibility.
26. Ayla auto-reschedule appears as booking lifecycle event, not raw chat.
27. Provider receives post-action notification after eligible auto-reschedule.
28. Provider approval is not required when backend policy/slot/billing checks pass.

---

## 25. Recommended next document

Next provider-side handoff:

```text
docs/screens/provider-calendar-schedule-flow.md
```

Reason:

Provider Booking Detail defines one booking. Calendar/Schedule defines the provider’s operational day/week and time availability. Without schedule, booking detail is reactive only; provider still lacks full control over working time.

Recommended sequence:

1. `provider-booking-detail-flow.md`
2. `provider-calendar-schedule-flow.md`
3. `provider-services-prices-flow.md`
4. `provider-messages-flow.md`
5. `solo-provider-bootstrap.md`

---

## Last verified

2026-05-29 — Canonicalized by Tau from Codex `provider-booking-detail-flow.updated.md`. Voice/canon verified: sage-green + Manrope + Lucide per `design-tokens.md`; «ты» register; Ayla-mediated messaging (NOT direct chat); status vocabulary aligned with `customer-records-flow.md` («Не состоялась» / «Отменена салоном» / «Оплата не подтверждена»); B11 review-prompt eligibility chain + blockers respected; solo-provider one-BotUser model per ADR-0008. Ayla auto-reschedule = booking lifecycle event, not approval request (Q-PBD-7 LOCKED).
