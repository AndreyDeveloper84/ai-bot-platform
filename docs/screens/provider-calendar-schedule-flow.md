# Provider Calendar / Schedule Flow

| Field | Value |
|---|---|
| **Date** | 2026-05-29 r2 (canonicalized by Tau) |
| **Designer** | Codex draft → Tau canonical consolidation |
| **Status** | Canonical — consolidated for Tech Lead review; updated for Smart Landing schedule prefill |
| **Surface** | Ayla Pro Mini App |
| **Audience** | Solo-master, team-master, salon admin, salon owner |
| **Primary persona** | Ольга — self-employed master, owner + admin + master |
| **Secondary personas** | Team-master, salon administrator, salon owner |
| **Phase** | P0 PRE_PILOT |
| **Channel** | Ayla Pro Mini App + manager-bot DM push |
| **Scope** | Provider-side calendar and schedule flow: day/week view, booking list, manual booking, schedule prefill from Smart Landing/template, time block, working hours, day-off, sick day, vacation, stale slot protection |
| **Screens** | 1 main calendar screen + 5 flows + 6 state screens |
| **Related docs** | `provider-booking-detail-flow.md`, `schedule-management-handoff.md`, `master-solo-surface.md`, `master-time-off-handoff.md`, `customer-cancellation-reschedule-flow.md` |
| **Severity** | P0 BLOCKER for provider operations |

---

## Foundation references

| Doc | Why it matters |
|---|---|
| `provider-booking-detail-flow.md` | Calendar appointments open into booking detail; booking detail defines one-booking operational actions |
| `schedule-management-handoff.md` | Existing schedule-management foundation: working hours, exceptions, time blocks, manual bookings, conflict handling |
| `master-solo-surface.md` | Solo-provider is first-class and needs direct operational control |
| `solo-provider-ux.md` | Defines universal solo-provider assumptions and role behavior |
| `master-time-off-handoff.md` | Sick day, vacation, planned leave and recurring pattern changes must align with formal leave semantics |
| `customer-cancellation-reschedule-flow.md` | Customer-facing reschedule/cancel/no-fault copy must stay consistent |
| `ayla-mediated-messaging.md` | Customer-facing updates go through Ayla, not raw direct master/customer messaging |
| `design-tokens.md` | Visual source of truth: sage-green, warm neutrals, Manrope, Lucide icons, 12dp cards, low-shadow UI |

---

## 0. Overview

### What this module is

`Provider Calendar / Schedule Flow` is the provider-side operational screen where a master, admin or salon owner sees and manages working time.

This screen answers:

> “Что у меня сегодня, где свободные окна, что занято, и как быстро изменить расписание без хаоса?”

It is the provider-side command center for:

- daily appointments;
- weekly workload;
- manual bookings;
- blocked time;
- working hours;
- prefilled schedule from Smart Landing or template;
- day-off;
- sick day;
- vacation;
- schedule conflicts;
- slot freshness.

---

### Why this matters

`Provider Booking Detail` shows one appointment.

`Provider Calendar / Schedule Flow` shows the whole working day.

Without this screen:

- provider sees booking details, but not full day context;
- manual offline bookings can create double-booking;
- master sick day may not close available slots;
- walk-in client can occupy a slot that Ayla still offers online;
- admin cannot quickly see who is free;
- solo-master has no simple place to manage working time;
- provider-side operations stay reactive instead of controlled.

This is especially critical for salons/masters that do not use external scheduling systems.

---

### Product promise

The provider should understand their schedule in under 10 seconds:

- what is next;
- what is free;
- what is blocked;
- what needs attention;
- where conflicts exist;
- what actions are allowed.

The flow must be mobile-first, fast and safe.

---

## 1. Personas

### 1.1. Ольга — solo-master

Ольга manages everything herself:

- sees her full day;
- adds manual bookings;
- blocks time;
- edits working hours;
- marks sick day/day-off;
- handles reschedule/cancel;
- sees which bookings need action.

For solo-master, the screen must be direct and low-friction.

---

### 1.2. Team-master

Team-master mostly sees their own schedule.

They can:

- view own day/week;
- open booking detail;
- request schedule change;
- request time-off;
- see upcoming appointments;
- react to urgent availability problems.

They cannot by default:

- edit salon-wide schedule;
- change another master’s calendar;
- cancel bookings directly;
- edit working hours without approval;
- block prime-time slots without admin awareness.

---

### 1.3. Salon administrator

Admin manages operational schedule:

- all masters;
- manual bookings;
- blocks;
- exceptions;
- reassignment;
- conflict resolution;
- same-day changes;
- requests from team-masters.

Admin is the primary daily operator for salon calendar.

---

### 1.4. Salon owner

Owner has full visibility and can override admin-level decisions.

Owner should not need to use this screen every hour, but can inspect and fix if needed.

---

## 2. User jobs

### Solo-master JTBD

> “Когда я веду свой день, я хочу сразу видеть записи и свободные окна, чтобы быстро добавить клиента, закрыть время или поменять график без администратора.”

### Team-master JTBD

> “Когда я открываю расписание, я хочу видеть свой день и быстро сообщить салону, если мне нужно закрыть время или изменить график.”

### Admin JTBD

> “Когда я управляю сменой, я хочу видеть все записи и свободные окна по мастерам, чтобы не допустить двойных записей и быстро решать изменения.”

### Owner JTBD

> “Когда я проверяю работу салона, я хочу видеть, что расписание контролируется, а мастера и администраторы не создают хаос.”

---


### Smart Landing prefill

Schedule can be created in three ways:

```text
1. From external sources: YCLIENTS / Яндекс.Карты / 2ГИС / website
2. From template preset: e.g. solo beauty default schedule
3. Manual input by provider/admin
```

All imported or template-generated working hours must be reviewed before go-live.

If sources conflict:

```text
Яндекс.Карты: 10:00–20:00
2ГИС: 10:00–19:00
Сайт: по записи
```

UI should show conflict and require user decision.

Template schedule must be labelled:

```text
Черновик графика по шаблону — проверьте перед публикацией.
```

## 3. Main navigation

> [!IMPORTANT]
> **CURRENT OVERRIDE — 25.08.2026**
>
> Навигационный контракт этого раздела superseded явным owner ruling.
>
> Для Salon Controlled Pilot CURRENT IA:
>
> `День · Команда · Услуги · Чаты · Настройки`
>
> Раздел нельзя убирать из навигации, пока его capability реально не покрыта другой Pilot surface.
>
> Override относится только к IA / §3. Остальная спецификация сохраняется как implementation provenance и действует там, где не отменена отдельно.
>
> ---
>
> *Примечание исполнителя (не часть owner ruling): §3 содержит* два *набора — `Salon/team` и `Solo-provider`. Override выше относится к* салонному *набору. Solo-набор подтверждён тем же решением владельца 25.08 (solo — отдельная operational persona) и исполнен рантаймом почти дословно (`App.tsx:598-602`). Разбор: `docs/UX_CANON_RECONCILIATION.md` §5.2.*

Provider-side bottom navigation can use different labels depending on provider type.

### Solo-provider

```text
Главная
Записи
Клиенты
Услуги
Профиль
```

Calendar lives inside:

```text
Записи → Календарь
```

or as the main content of `Записи`.

### Salon/team

```text
Главная
Календарь
Клиенты
Команда
Профиль
```

For salon/team, calendar deserves a top-level tab because admin/owner needs multi-master operational control.

---

## 4. Entry points

### E1. From bottom navigation

Provider taps:

```text
Записи
```

or:

```text
Календарь
```

depending on role/surface.

---

### E2. From “Мой день” / dashboard

Provider taps:

```text
Открыть расписание
```

or a time block/appointment summary.

---

### E3. From booking detail

Provider taps:

```text
Открыть в календаре
```

or:

```text
Выбрать другое время
```

---

### E4. From manager-bot DM

Manager-bot push opens calendar in context.

Push examples:

- new manual booking request;
- team-master schedule change request;
- sick day;
- impacted bookings;
- double-booking warning;
- external sync conflict;
- stale slot warning.

---

### E5. From customer / client card

Admin or solo-master opens customer profile and chooses:

```text
Создать запись
```

Then calendar opens with booking creation flow.

---

## 5. Screen structure

Main calendar screen has 7 blocks.

```text
B1 — Header / date switcher
B2 — View toggle: День / Неделя / Список
B3 — Master filter
B4 — Timeline / appointment list
B5 — Free slots
B6 — Conflicts / attention cards
B7 — Quick actions
```

---

## 6. B1 — Header / date switcher

### Purpose

Shows current date and lets provider move between days.

Example:

```text
Сегодня, 28 мая
Среда
```

Controls:

```text
[←] [Сегодня] [→]
```

Optional:

```text
[Календарь]
```

### Rules

- Default date is today.
- “Сегодня” button returns to current day.
- Use tenant/provider timezone.
- If timezone mismatch exists, show source of truth from backend.

---

## 7. B2 — View toggle

### Views

| View | Purpose |
|---|---|
| День | main operational view |
| Неделя | workload and planning |
| Список | compact appointment list |

### MVP default

Default view:

```text
День
```

Reason:

Most providers need today first.

---

## 8. B3 — Master filter

### Solo-master

Hidden or static:

```text
Ольга
```

### Team-master

Hidden or static:

```text
Моё расписание
```

### Admin/Owner

Visible filter:

```text
Все мастера
Ольга
Анна
Мария
```

Optional grouping:

```text
На смене
Свободны
Заняты
```

### Rules

- Team-master must not access other masters’ calendars by changing frontend params.
- Admin/owner can filter by master.
- “Все мастера” view should not become visually overloaded; if too many masters, use list mode.

---

## 9. B4 — Timeline / appointment list

### Purpose

Shows occupied time.

Example:

```text
10:00–11:00
Анна С. · Массаж спины
Ольга · Подтверждена

11:15–12:00
Мария К. · LPG
Ольга · Клиент написал
```

Tap on appointment opens:

```text
provider-booking-detail-flow.md
```

### Status indicators

Use calm labels:

- Подтверждена
- Клиент написал
- Оплата не подтверждена
- Перенесена
- Требует внимания

Do not use aggressive labels like:

- Проблема
- Просрочено
- Клиент не оплатил

---

## 10. B5 — Free slots

### Purpose

Shows available time windows.

Example:

```text
Свободные окна сегодня

12:30–13:30
15:00–16:00
18:30–19:00
```

Actions:

```text
[Добавить запись]
[Заблокировать время]
```

### Important

Free slots must be calculated from:

```text
WorkingHours
- ScheduleException
- TimeBlock
- existing BookingRequest
- service duration
- buffer between bookings
```

Frontend must not calculate authoritative availability alone.

### Slot freshness

Every mutation must re-check slot availability server-side.

Frontend availability is only a preview.

---

## 11. B6 — Conflicts / attention cards

### Examples

```text
2 записи требуют внимания
```

Types:

- payment not confirmed;
- customer message unanswered;
- master unavailable;
- booking conflict;
- stale schedule;
- overlapping manual booking;
- external sync conflict if integration exists;
- impacted bookings after sick day/day-off.

### UX rule

Attention cards should be actionable, not scary.

Good:

```text
Есть пересечение по времени. Откройте, чтобы выбрать действие.
```

Bad:

```text
Критическая ошибка расписания!
```

---

## 12. B7 — Quick actions

### Common actions

```text
Добавить запись
Заблокировать время
Изменить часы
Выходной
Отпуск / больничный
```

### Role-based visibility

| Action | Solo-master | Team-master | Admin | Owner |
|---|---:|---:|---:|---:|
| Add manual booking | yes | optional/request | yes | yes |
| Block time | yes | request by default | yes | yes |
| Edit working hours | yes | request only | yes | yes |
| Day-off | yes | request/admin-aware | yes | yes |
| Sick day | yes | yes, notifies admin | yes | yes |
| Vacation | request/submit | request | approve/manage | approve/manage |
| View all masters | no | no | yes | yes |

---

## 13. Flow F1 — Add manual booking

### Purpose

Provider creates booking for a customer who came from outside Ayla.

Examples:

- client called;
- client wrote in VK/WhatsApp;
- walk-in client;
- client booked after visit at reception.

### Flow

```text
Tap free slot
→ Add manual booking
→ Choose/create customer
→ Choose service
→ Choose master
→ Confirm time
→ Save booking
```

### Fields

- customer;
- phone optional;
- service;
- master;
- date/time;
- duration;
- price;
- note;
- source.

### Attribution rule

Manual booking is not AI-direct.

Default source:

```text
human_direct
```

### Slot freshness rule

If selected slot is no longer available:

```text
Это время уже занято. Показываю свободные варианты.
```

Server must reject stale manual booking creation.

---

## 14. Flow F2 — Block time

### Purpose

Provider blocks time without creating customer booking.

Examples:

- lunch;
- cleaning;
- personal break;
- preparation;
- admin work;
- equipment maintenance.

### Flow

```text
Select free slot
→ Block time
→ Choose duration
→ Add reason
→ Save
```

### Fields

- start time;
- end time;
- reason;
- master;
- repeat optional post-MVP.

### Copy

Use:

```text
Заблокировать время
```

Not:

```text
Скрыть слот
```

Reason:

“Hide” sounds suspicious; “block time” is operational.

### Team-master rule

Team-master cannot directly block time by default.

Team-master creates request:

```text
Попросить заблокировать время
```

Exception:

- sick/emergency unavailable flow can apply immediately with admin notification.

---

## 15. Flow F3 — Edit working hours

### Purpose

Change recurring availability.

Examples:

- work Mon-Fri 10:00–19:00;
- Saturday 11:00–16:00;
- Sunday off.

### Solo-master

Can edit directly, with confirmation.

### Team-master

Creates change request.

### Admin/Owner

Can edit directly for any master.

### Confirmation copy

```text
Изменить рабочие часы?

Это повлияет на свободные окна для будущих записей.
Существующие записи не изменятся автоматически.
```

### Existing bookings rule

Existing bookings outside new working hours remain active.

Show warning and impacted bookings link.

---

## 16. Flow F4 — Day-off / sick day

### Sick day

Same-day urgent flow.

Solo-master/team-master can mark sick day, but admin is notified.

Copy:

```text
Отметить больничный на сегодня?

Ayla закроет свободные окна и покажет записи, которые нужно перенести.
Клиентам не будет показана причина.
```

Customer-facing message must never say:

```text
мастер заболел
```

Customer-facing framing:

```text
У мастера изменились планы на этот день. Я помогу выбрать другое время.
```

### Day-off

Non-urgent date exception.

Copy:

```text
Сделать день выходным?

Свободные окна на эту дату закроются.
Если уже есть записи, нужно будет выбрать действие по каждой.
```

### Impacted bookings rule

Day-off/sick day with existing bookings creates impacted-bookings tasks.

No instant mass-cancel.

Provider/admin must confirm customer-facing action.

---

## 17. Flow F5 — Vacation / planned leave

### Purpose

Longer planned absence.

Flow:

```text
Choose dates
→ Add optional internal reason
→ Preview impacted bookings
→ Submit / approve
→ Resolve impacted bookings
```

### Role rules

- Solo-master can create leave directly, but impacted bookings must be resolved.
- Team-master submits request.
- Admin/owner approves.
- Customer never sees raw reason.

---

## 18. Locked decisions

### Q-PCS-1 — Solo-master manual booking

**Decision:** LOCKED.

Solo-master can create manual bookings directly.

Server must re-check slot freshness before save.

Manual booking attribution:

```text
human_direct
```

Reason:

Solo-master receives bookings outside Ayla and must be able to protect slots from double-booking.

---

### Q-PCS-2 — Team-master block time

**Decision:** LOCKED.

Team-master cannot directly block time by default.

Team-master creates block-time request.

Emergency sick/unavailable flow can apply immediately with admin notification.

Direct block-time permission may become salon-level setting post-MVP.

---

### Q-PCS-3 — Manual bookings in customer Records

**Decision:** LOCKED.

Linked manual bookings appear in customer Records.

Unlinked manual bookings remain provider-only until customer match.

#### Rule

If manual booking has:

```text
customer_id
or linked BotUser
or verified customer phone match
```

then customer can see it in Records.

If not linked, the booking stays provider-only.

---

### Q-PCS-4 — Existing bookings outside new working hours

**Decision:** LOCKED.

Existing bookings outside new working hours remain active.

Working-hours change affects future free slots only.

Show warning and impacted bookings link.

---

### Q-PCS-5 — Sick day and customer rebooking

**Decision:** LOCKED.

Sick day closes free slots and creates impacted-bookings rebooking tasks.

No instant mass-cancel.

Customer message is sent only after provider/admin confirms action.

Customer-facing copy uses neutral Ayla voice and does not reveal medical/personal reason.

---

## 19. State matrix

### S1. Loading

Show skeleton:

- date header;
- view toggle;
- appointment cards;
- quick actions.

No blank screen.

---

### S2. Empty day

```text
На сегодня записей нет.

Можно добавить запись вручную или открыть свободные окна.
```

CTA:

```text
[Добавить запись]
[Заблокировать время]
```

---

### S3. Fully booked

```text
Сегодня свободных окон нет.
```

CTA:

```text
[Открыть неделю]
```

---

### S4. No working hours

```text
На этот день рабочие часы не настроены.
```

CTA:

```text
[Настроить часы]
```

---

### S5. Offline

```text
Нет соединения.

Показываю последние сохранённые данные. Изменения временно недоступны.
```

Mutation actions disabled.

---

### S6. Permission denied

```text
Нет доступа к этому расписанию.
```

CTA:

```text
[Вернуться]
[Сообщить администратору]
```

---

## 20. Edge cases

### 20.1. Double booking risk

If slot was free when user opened screen but became busy before saving:

```text
Это время уже занято. Выберите другое свободное окно.
```

Server must reject stale booking creation.

---

### 20.2. Existing bookings on day-off

If provider marks day-off where bookings exist:

```text
На этот день уже есть 3 записи.

Нужно выбрать действие:
- предложить перенос;
- назначить другого мастера;
- отменить с уведомлением.
```

---

### 20.3. Time block overlaps booking

Do not allow by default.

Copy:

```text
Это время занято записью. Сначала откройте запись и выберите действие.
```

---

### 20.4. Master edits hours but existing booking is outside new hours

Existing bookings remain active.

Show warning:

```text
Есть записи вне новых рабочих часов. Они не изменятся автоматически.
```

---

### 20.5. Integration sync conflict

If external calendar/YClients changes schedule:

```text
Расписание обновилось из внешней системы.

Проверьте изменения перед новыми записями.
```

MVP can show read-only conflict banner if integration conflict handling is not ready.

---

### 20.6. Unlinked manual booking

If manual booking has no linked customer:

```text
Запись сохранена только в расписании.

Когда клиент будет найден в Ayla, запись можно будет связать с его профилем.
```

---

### 20.7. Team-master urgent unavailable

If team-master marks urgent unavailable:

- close their free slots;
- notify admin;
- create impacted-bookings queue;
- do not instantly message all customers without admin/provider confirmation.

---

## 21. Confirmation modals

### M1. Add manual booking

```text
Добавить запись?

Анна С. · Массаж спины
Завтра, 16:00 · 60 минут

Источник: вручную

[Добавить запись]
[Назад]
```

---

### M2. Block time

```text
Заблокировать время?

Завтра, 13:00–13:30
Причина: обед

Ayla не будет предлагать это окно клиентам.

[Заблокировать]
[Назад]
```

---

### M3. Edit working hours

```text
Изменить рабочие часы?

Это повлияет на будущие свободные окна.
Существующие записи останутся активными.

[Изменить]
[Назад]
```

---

### M4. Day-off with bookings

```text
На этот день уже есть записи

Перед тем как сделать день выходным, нужно решить, что делать с каждой записью.

[Открыть записи]
[Назад]
```

---

### M5. Sick day

```text
Отметить больничный?

Свободные окна закроются.
Записи на этот день попадут в список для переноса.

Клиентам не будет показана причина.

[Продолжить]
[Назад]
```

---

## 22. Provider-side CTA vocabulary

### Approved CTA

```text
Открыть запись
Добавить запись
Заблокировать время
Изменить часы
Сделать выходным
Отметить больничный
Попросить изменить график
Попросить заблокировать время
Открыть свободные окна
Открыть неделю
Выбрать мастера
Предложить перенос
Назначить другого мастера
```

### Forbidden CTA

```text
Скрыть слот
Клиент не пришёл
Мастер заболел клиенту
Критическая ошибка
Убрать запись
Удалить клиента
Заблокировать клиента
```

---

## 23. Role-based visibility matrix

| Block / action | Solo-master | Team-master | Admin | Owner |
|---|---:|---:|---:|---:|
| Day view | yes | own only | all | all |
| Week view | yes | own only | all | all |
| Master filter | no/static | no/static | yes | yes |
| Add manual booking | yes | request/optional | yes | yes |
| Block time | yes | request by default | yes | yes |
| Edit working hours | yes | request only | yes | yes |
| Day-off | yes | request/admin-aware | yes | yes |
| Sick day | yes | yes + admin notified | yes | yes |
| Vacation | submit/manage own | request | approve/manage | approve/manage |
| View impacted bookings | yes | own only | all relevant | all relevant |
| Resolve impacted bookings | yes if own/solo | no by default | yes | yes |
| External sync conflicts | yes if own | no/limited | yes | yes |

---

## 24. API draft

### 24.1. GET day schedule

```text
GET /api/v1/provider/schedule/day?date=2026-07-15&master_id=uuid
```

Response draft:

```json
{
  "date": "2026-07-15",
  "timezone": "Europe/Moscow",
  "master_filter": {
    "selected_master_id": "uuid",
    "available_masters": [
      {
        "id": "uuid",
        "display_name": "Ольга",
        "status": "working"
      }
    ]
  },
  "working_hours": {
    "start": "10:00",
    "end": "19:00",
    "is_working": true
  },
  "bookings": [
    {
      "id": "uuid",
      "starts_at": "2026-07-15T10:00:00+03:00",
      "ends_at": "2026-07-15T11:00:00+03:00",
      "client_display_name": "Анна С.",
      "service_name": "Массаж спины",
      "status": "confirmed",
      "status_label": "Подтверждена"
    }
  ],
  "time_blocks": [
    {
      "id": "uuid",
      "starts_at": "2026-07-15T13:00:00+03:00",
      "ends_at": "2026-07-15T13:30:00+03:00",
      "reason": "Обед"
    }
  ],
  "free_slots": [
    {
      "starts_at": "2026-07-15T12:00:00+03:00",
      "ends_at": "2026-07-15T13:00:00+03:00"
    }
  ],
  "attention_items": [
    {
      "type": "customer_message",
      "label": "Клиент написал",
      "booking_id": "uuid"
    }
  ]
}
```

---

### 24.2. GET week schedule

```text
GET /api/v1/provider/schedule/week?start_date=2026-07-13&master_id=uuid
```

---

### 24.3. POST manual booking

```text
POST /api/v1/provider/bookings/manual
```

Payload:

```json
{
  "customer_id": "uuid_or_null",
  "customer_phone": "+79990000000",
  "customer_name": "Анна",
  "service_id": "uuid",
  "master_id": "uuid",
  "starts_at": "2026-07-15T16:00:00+03:00",
  "duration_minutes": 60,
  "note": "Записалась по телефону",
  "source": "human_direct"
}
```

---

### 24.4. POST time block

```text
POST /api/v1/provider/schedule/time-blocks
```

Payload:

```json
{
  "master_id": "uuid",
  "starts_at": "2026-07-15T13:00:00+03:00",
  "ends_at": "2026-07-15T13:30:00+03:00",
  "reason": "Обед"
}
```

---

### 24.5. POST working hours update

```text
POST /api/v1/provider/schedule/working-hours
```

---

### 24.6. POST exception

```text
POST /api/v1/provider/schedule/exceptions
```

Payload:

```json
{
  "master_id": "uuid",
  "date": "2026-07-15",
  "type": "sick_day",
  "internal_reason": "optional_admin_visible_only"
}
```

---

### 24.7. POST schedule change request

```text
POST /api/v1/provider/schedule/change-requests
```

Payload:

```json
{
  "requested_change_type": "time_block",
  "master_id": "uuid",
  "starts_at": "2026-07-15T13:00:00+03:00",
  "ends_at": "2026-07-15T13:30:00+03:00",
  "reason": "Нужна подготовка кабинета"
}
```

---

## 25. Events

### Required events

```text
provider.schedule.day.opened
provider.schedule.week.opened
provider.schedule.master_filter.changed
provider.schedule.manual_booking.started
provider.schedule.manual_booking.created
provider.schedule.manual_booking.slot_stale
provider.schedule.time_block.started
provider.schedule.time_block.created
provider.schedule.time_block.requested
provider.schedule.working_hours.update_started
provider.schedule.working_hours.updated
provider.schedule.day_off.started
provider.schedule.day_off.created
provider.schedule.sick_day.started
provider.schedule.sick_day.created
provider.schedule.impacted_bookings.created
provider.schedule.permission_denied
provider.schedule.offline_viewed
provider.schedule.sync_conflict.viewed
```

### Important payload fields

```json
{
  "tenant_id": "uuid",
  "actor_id": "uuid",
  "actor_role": "admin",
  "master_id": "uuid",
  "date": "2026-07-15",
  "source_surface": "ayla_pro_mini_app"
}
```

---

## 26. Frontend implementation notes

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

### Mobile-first layout order

```text
1. Header/date switcher
2. View toggle
3. Master filter if available
4. Attention cards if any
5. Timeline/list
6. Free slots
7. Quick actions
```

### Sticky action area

Use sticky bottom CTA for primary action when appropriate:

- `Добавить запись`;
- `Заблокировать время`;
- `Открыть свободные окна`.

Avoid more than one primary sticky CTA at the same time.

---

## 27. Accessibility

Minimum requirements:

- body text 16px equivalent;
- tap targets ≥ 44px;
- day/week navigation reachable by screen reader;
- status labels not color-only;
- schedule conflicts described with text;
- destructive/schedule-wide actions require confirmation;
- reduced motion respected;
- empty/offline states readable;
- icons paired with text;
- week view must have list fallback for small screens.

---

## 28. MVP scope

### In scope

- day view;
- week view;
- compact list view;
- role-based master filter;
- booking cards;
- free slots;
- manual booking;
- time block;
- working hours edit/request;
- day-off;
- sick day;
- impacted bookings queue;
- stale slot protection;
- loading/empty/offline/permission states.

---

### Out of scope

- drag-and-drop calendar editing;
- recurring time blocks;
- Google Calendar sync;
- advanced YClients conflict resolution;
- auto-assign replacement master;
- master-to-master shift swap;
- public customer-facing schedule editor;
- complex multi-branch calendars;
- payroll impact calculation;
- full vacation approval queue details;
- HR/legal leave accounting.

---

## 29. Acceptance criteria

1. Provider can view day schedule.
2. Provider can view week schedule.
3. Solo-master sees own schedule by default.
4. Team-master sees own schedule only.
5. Admin/owner can filter by master.
6. Appointment tap opens provider booking detail.
7. Free slots are shown based on backend availability.
8. Manual booking checks slot freshness server-side.
9. Manual booking is attributed as `human_direct`.
10. Solo-master can create manual booking directly.
11. Linked manual booking appears in customer Records.
12. Unlinked manual booking stays provider-only.
13. Provider can block free time if role allows.
14. Team-master block-time creates request by default.
15. Provider cannot block time over existing booking without resolving booking.
16. Working hours changes do not silently move existing bookings.
17. Existing bookings outside new working hours remain active.
18. Working-hours change warning links to impacted bookings.
19. Day-off with existing bookings requires conflict resolution.
20. Sick day closes free slots.
21. Sick day creates impacted-bookings rebooking tasks.
22. Sick day does not instantly mass-cancel bookings.
23. Sick day does not reveal medical reason to customer.
24. Customer-facing schedule-change copy uses neutral Ayla voice.
25. Offline state disables mutation actions.
26. Permission denied state is covered.
27. No empty blank calendar.
28. All destructive or schedule-wide actions require confirmation.
29. Role-based permissions enforced server-side.
30. Events emitted for major schedule actions.
31. Schedule can be prefilled from Smart Landing sources or template preset.
32. Imported/template working hours require review before go-live.
33. Conflicting hours from multiple sources are shown and resolved by provider/admin.

---

## 30. Recommended next document

Next provider-side handoff:

```text
docs/screens/provider-services-prices-flow.md
```

Reason:

Schedule defines when provider works. Services & Prices define what provider sells and how booking duration/price are calculated.

Recommended sequence:

1. `provider-booking-detail-flow.md`
2. `provider-calendar-schedule-flow.md`
3. `provider-services-prices-flow.md`
4. `provider-messages-flow.md`
5. `solo-provider-bootstrap.md`

---

## Last verified

2026-05-29 — Canonicalized by Tau from Codex `provider-calendar-schedule-flow.smart-landing-updated.md`. Voice/canon verified: sage-green + Manrope + Lucide per `design-tokens.md`; **two-axis register (founder verdict 2026-05-29): provider-facing UI copy = «вы», customer-facing Ayla quotes = «ты»**; manual booking attribution `human_direct` per `attribution-extensible-model`; slot freshness re-checked server-side (frontend availability is preview only); sick-day/day-off never reveal reason to customer (neutral Ayla framing); impacted-bookings queue instead of instant mass-cancel; Smart Landing / template schedule prefill requires review before go-live.

Register sweep 2026-05-29: provider attention/error copy → «вы» («Откройте, чтобы выбрать действие» / «Выберите другое свободное окно» / «Проверьте изменения»).
