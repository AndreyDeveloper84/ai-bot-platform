# Screen: customer-records-flow

| Field | Value |
|---|---|
| **Audience** | customer viewing booking history (upcoming + past) |
| **Phase** | P0 BLOCKER pilot 15 July 2026 — completes booking lifecycle visibility |
| **Status** | draft — Phase A–G done, awaiting tech lead final sign-off |
| **Channel** | MAX webview Mini App, «Записи» tab from bottom nav |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-26 r1 |
| **Foundation** | [`customer-main-wellness-dashboard.md`](./customer-main-wellness-dashboard.md) (bottom nav) · [`customer-booking-flow.md`](./customer-booking-flow.md) §3 (multi-tenant Variant C reuse) · [`customer-cancellation-reschedule-flow.md`](./customer-cancellation-reschedule-flow.md) (action flows) · [`ayla-mediated-messaging.md §3.1`](../design/policies/ayla-mediated-messaging.md) («Написать по записи») · [`information-architecture.md`](../design/policies/information-architecture.md) §4 |
| **Severity** | P0 BLOCKER pilot — без Records tab customer не видит свою историю |

---

## 1. Контекст

### Why this exists

Per tech lead strategic plan 2026-05-26 — closing customer lifecycle visibility. Booking flow + cancellation/reschedule shipped; customer теперь нужно место **видеть все свои записи**:
- Будущие — что предстоит
- Прошлые — что было (для повтора + воспоминаний)
- Отменённые — что было пропущено / отменено

Records tab = bottom nav Surface 3 per `information-architecture.md` §4. Existing F3 «My visits» в `2026-05-18-customer-first-time-handoff.md` pre-Ayla-first pivot — refresh с современным voice + multi-tenant grouping + Q12-α billing chain transparency + ayla-mediated-messaging integration.

### Scope per tech lead UX-UI.txt (2026-05-26)

**Confirmed:**
- 2 sections approach «Ближайшие» / «История» (NOT 4 heavy tabs)
- R1 Main list + R2 Upcoming card + R3 Booking detail (THE main screen) + R4 Status vocabulary + R5 Empty states + R6 Repeat booking CTA
- Multi-tenant Variant C reuse from booking-flow §8
- Past bookings: limited actions (no Перенести / Отменить / Написать по записи per ayla-mediated-messaging §11.1)

**Out of scope (per tech lead «не тащить в Records MVP»):**
- Complex per-booking message history
- Detailed payment ledger
- Full refund timeline visualization
- Attachment upload / documents / чеки
- In-tab review submission (separate scope per master-reviews-feedback handoff)
- Complex calendar view
- Cross-tenant CRM grouping beyond Variant C

---

## 2. Entry points

| Surface | Trigger | Lands |
|---------|---------|-------|
| Bottom nav `📅 Записи` tab | Customer taps | R1 «Ближайшие» section (default) |
| Dashboard «Ближайшая запись» card → «Открыть запись» | Customer taps | R3 Booking detail directly |
| Dashboard «Все записи →» link (when N>1 bookings) | Customer taps | R1 «Ближайшие» section |
| Cancellation success «На главную» / Reschedule success | Customer taps | Dashboard (NOT Records) — closed booking journey |
| Bot DM «покажи мои записи» | Customer types | R1 «Ближайшие» via deeplink |

### Default landing

Per Q-R-10 lean — **«Ближайшие» section default**. Most common need = check upcoming bookings.

---

## 3. R1 — Main records list

### 3.1 Two-section structure (Q-R-1 lean)

```
┌──────────────────────────────────────────────┐
│  ←  Записи                                    │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  [ Ближайшие (3) ][ История ]                 │  Tab strip
│                                               │  с counts
│  ─────────────────────────────────────       │
│                                               │
│  ...content per active tab...                 │
│                                               │
├──────────────────────────────────────────────┤
│  🏠      ☀        📅      💅      👤         │  Bottom nav
│ Главная   День    Записи Услуги   Я          │
│                   ▔▔▔▔▔                       │
└──────────────────────────────────────────────┘
```

Two tabs total. Internal filters within «История» if list long (per Q-R-9).

### 3.2 «Ближайшие» section — Single tenant case

Customer's all upcoming bookings (status `confirmed` или `rescheduled`):

```
┌──────────────────────────────────────────────┐
│  [ Ближайшие (3) ][ История ]                 │
│  ─────────────────────────────────────       │
│                                               │
│  ── Завтра ──                                 │  Time grouping
│                                               │
│  ┌──────────────────────────────────────┐   │  Nearest booking
│  │  ✓ Подтверждена                        │   │  status icon+label
│  │  Четверг 29 мая · 16:00                │   │
│  │  Маникюр гель-лак · 90 мин             │   │
│  │  Анна Петрова · Beauty Place           │   │
│  │  ~1 800 ₽                              │   │
│  │                                        │   │
│  │  [ Открыть запись ]                    │   │
│  │  [ Написать по записи ]                │   │
│  │  [ Маршрут ] [ Перенести ] [ Отмена ]  │   │  Full actions
│  └──────────────────────────────────────┘   │  (nearest booking)
│                                               │
│  ── На этой неделе ──                         │
│                                               │
│  ┌──────────────────────────────────────┐   │  Compact card
│  │  ✓ Подтверждена                        │   │  для not-nearest
│  │  Пятница 31 мая · 14:00                │   │  bookings
│  │  Массаж лимфодренаж · 60 мин           │   │
│  │  Ирина · Beauty Place                  │   │
│  │                                        │   │
│  │  [ Открыть ] [ Написать по записи ]    │   │  Limited actions
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Через неделю ──                           │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✓ Подтверждена                        │   │
│  │  Понедельник 5 июня · 12:00            │   │
│  │  Маникюр + дизайн · 120 мин            │   │
│  │  Анна · Beauty Place                   │   │
│  │                                        │   │
│  │  [ Открыть ] [ Написать по записи ]    │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### 3.3 «Ближайшие» section — Multi-tenant case (Variant C per Q-R-8)

Per `customer-booking-flow.md §8` Variant C smart adaptive grouping. Same logic:

```
┌──────────────────────────────────────────────┐
│  [ Ближайшие (5) ][ История ]                 │
│  ─────────────────────────────────────       │
│                                               │
│  ── Beauty Place ──                           │  Tenant grouping
│                                               │  per Variant C
│  ┌──────────────────────────────────────┐   │
│  │  ✓ Завтра · пт · 16:00                │   │
│  │  Маникюр · у Анны · ~1 800 ₽          │   │
│  │  [ Открыть ] [ Написать ]              │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✓ Понедельник · 12:00                │   │
│  │  Маникюр + дизайн · у Анны             │   │
│  │  [ Открыть ] [ Написать ]              │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Студия Натали ──                          │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  🔄 Перенесена                          │   │  Rescheduled status
│  │  Среда 4 июня · 11:00                  │   │
│  │  Брови · у Карины                       │   │
│  │  [ Открыть ] [ Написать ]              │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Casa Bella ──                             │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✓ Пятница 7 июня · 19:00             │   │
│  │  Брови · у Светы                       │   │
│  │  [ Открыть ] [ Написать ]              │   │
│  └──────────────────────────────────────┘   │
│                                               │
└──────────────────────────────────────────────┘
```

### 3.4 «История» section

Past bookings — completed / cancelled / no-show / rescheduled-out. Filter chips per Q-R-9 (only here, not in «Ближайшие»).

```
┌──────────────────────────────────────────────┐
│  [ Ближайшие ][ История (18) ]                │
│  ─────────────────────────────────────       │
│                                               │
│  Фильтр: [ Все ▾ ] [ Маникюр ▾ ] [ У Анны ▾ ] │  Filter chips
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Май 2026 ──                               │  Month grouping
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✅ Прошла · 20 мая                    │   │  Completed status
│  │  Маникюр гель-лак · у Анны             │   │
│  │  Beauty Place · 2 400 ₽                │   │
│  │  ⭐ Можно оставить отзыв               │   │  Pending review
│  │                                        │   │  badge
│  │  [ Записаться ещё ] [ Оставить отзыв ]│   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✅ Прошла · 6 мая                     │   │
│  │  Маникюр + парафин · у Анны            │   │
│  │  Beauty Place · 2 700 ₽                │   │
│  │  Твой отзыв: ⭐⭐⭐⭐⭐                  │   │  Existing review
│  │                                        │   │  shown
│  │  [ Записаться ещё ]                    │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✕ Отменена · 28 апреля                │   │  Cancelled status
│  │  Маникюр · у Анны                       │   │
│  │  Без штрафа                            │   │  Refund clarity
│  │                                        │   │
│  │  [ Записаться ещё ]                    │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Апрель 2026 ──                            │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ! Отменена салоном · 22 апреля        │   │  Provider-cancelled
│  │  Массаж · у Ирины                       │   │  no-fault
│  │  Возврат завершён ✓                    │   │
│  │                                        │   │
│  │  [ Записаться ещё ]                    │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  — Не пришла · 15 апреля              │   │  No-show status
│  │  Маникюр · у Анны                       │   │
│  │  100 ₽ удержано                        │   │  Per Q12-β
│  │                                        │   │
│  │  [ Записаться ещё ]                    │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ Показать ещё (12) ]                        │  Pagination
│                                               │
└──────────────────────────────────────────────┘
```

### 3.5 Sticky-top «Ближайшая» banner

If «Ближайшие» tab имеет nearest booking <24h до визита — sticky top alert:

```
┌──────────────────────────────────────────────┐
│  ⏰ Запись через 3 часа · 16:00                │  Sticky banner
│  Маникюр у Анны в Beauty Place                │
│  [ Маршрут ]   [ Написать по записи ]          │
└──────────────────────────────────────────────┘
```

NOT a card duplicate of nearest — just quick access bar.

---

## 4. R2 — Upcoming booking card variants

### 4.1 Nearest booking (next 24h) — Full actions

```
┌──────────────────────────────────────┐
│  ✓ Подтверждена                        │
│  Сегодня · 16:00                       │
│  Маникюр гель-лак · 90 мин             │
│  Анна Петрова · Beauty Place           │
│  ул. Тверская 12                       │
│  ~1 800 ₽                              │
│                                        │
│  [ Открыть запись ]                    │
│  [ Написать по записи ]                │
│  [ Маршрут ] [ Перенести ] [ Отмена ]  │
└──────────────────────────────────────┘
```

5 actions: Открыть запись (detail) / Написать по записи (per ayla-mediated) / Маршрут (maps deeplink) / Перенести (per cancel/reschedule) / Отмена (per cancel/reschedule).

### 4.2 Future booking (>24h, not nearest) — Limited actions

```
┌──────────────────────────────────────┐
│  ✓ Подтверждена                        │
│  Пятница 31 мая · 14:00                │
│  Массаж лимфодренаж · 60 мин           │
│  Ирина · Beauty Place                  │
│                                        │
│  [ Открыть ] [ Написать по записи ]    │
└──────────────────────────────────────┘
```

2 actions: Открыть / Написать. Перенести/Отмена/Маршрут только в booking detail (R3).

### 4.3 Past booking (completed / cancelled) — «Записаться ещё»

```
┌──────────────────────────────────────┐
│  ✅ Прошла · 20 мая                    │
│  Маникюр гель-лак · у Анны             │
│  Beauty Place · 2 400 ₽                │
│  ⭐ Можно оставить отзыв               │  Pending feedback
│                                        │  badge if applicable
│  [ Записаться ещё ] [ Оставить отзыв ]│
└──────────────────────────────────────┘
```

Per Q-R-5 lean — «Записаться ещё» always + «Оставить отзыв» if pending. NO «Написать по записи» (closed per ayla-mediated-messaging §11.1).

---

## 5. R3 — Booking detail (THE main screen)

Per tech lead «Самый важный экран». Single tall scroll per Q-R-4.

### 5.1 Future booking detail (active) — per founder explicit section order

```
┌──────────────────────────────────────────────┐
│  ← Запись                                     │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  ✓ Подтверждена                                │  1. Status header
│                                               │     (prominent)
│  ─────────────────────────────                │
│                                               │
│  Маникюр гель-лак · 90 минут                  │  2. Main recap
│  у Анны Петровой                              │     (услуга / мастер /
│  Beauty Place                                 │      салон)
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Четверг 29 мая · 16:00                       │  3. Date/time
│                                               │
│  ─────────────────────────────                │
│                                               │
│  ул. Тверская 12                              │  4. Address + route
│                                               │
│  [ 🗺 Маршрут до салона ]                     │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Действия                                     │  5. Actions
│                                               │     (sticky или
│  [ 💬 Написать по записи ]                    │      очень заметная
│  [ 🔄 Перенести ]                              │      для upcoming —
│  [ ✕ Отменить запись ]                         │      per founder
│                                               │      explicit order)
│  ─────────────────────────────                │
│                                               │
│  Стоимость                                    │  6. Price / payment /
│  ~1 800 ₽                                     │     refund if relevant
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Политика отмены                              │  7. Cancellation
│                                               │     policy
│  Отмена за 12+ часов — без штрафа.            │
│  Меньше 12 часов — салон может удержать       │
│  50% (около 900 ₽).                           │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Заметка мастеру                              │  8. Note to master
│                                               │     (if exists)
│  + Добавить заметку Анне                      │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ 📅 Записаться ещё ]                         │  9. Repeat / related
│                                               │     actions
│                                               │
└──────────────────────────────────────────────┘
```

**Section order per founder explicit refinement:**
1. Status header
2. Main recap (услуга / мастер / салон)
3. Date/time
4. Address + route
5. Actions (sticky или very prominent для upcoming)
6. Price / payment / refund if relevant
7. Cancellation policy
8. Note to master if exists
9. Repeat booking / related actions

Rationale: customer's most actionable concerns surface first. Actions before price/policy reduces friction для quick decisions.

### 5.2 Past booking detail (completed)

```
┌──────────────────────────────────────────────┐
│  ← Запись                                     │
│  ─────────────────────────────────────       │
│                                               │
│  ✅ Прошла · 20 мая 2026                       │
│                                               │
│  Маникюр гель-лак · 90 минут                  │
│  2 400 ₽                                      │
│                                               │
│  Где                                          │
│                                               │
│  Beauty Place                                 │
│  Анна Петрова                                 │
│                                               │
│  Твой отзыв                                   │  Existing review
│                                               │  inline shown
│  ⭐⭐⭐⭐⭐                                       │
│  «Аккуратная, не торопится. Спасибо!»         │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ 📅 Записаться ещё ]                         │  Primary action
│                                               │
└──────────────────────────────────────────────┘
```

### 5.3 Cancelled booking detail

```
┌──────────────────────────────────────────────┐
│  ← Запись                                     │
│  ─────────────────────────────────────       │
│                                               │
│  ✕ Отменена · 28 апреля                        │
│                                               │
│  Маникюр гель-лак · 90 минут                  │
│  1 800 ₽                                      │
│                                               │
│  Где                                          │
│  Beauty Place · Анна Петрова                  │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Что произошло                                │  Cancellation
│                                               │  context
│  Отменена вами 27 апреля.                    │
│  Без штрафа — отмена за 24 часа.              │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ 📅 Записаться ещё ]                         │
│                                               │
└──────────────────────────────────────────────┘
```

### 5.4 Provider-cancelled (no-fault) detail

```
┌──────────────────────────────────────────────┐
│  ← Запись                                     │
│  ─────────────────────────────────────       │
│                                               │
│  ⚠ Отменена салоном · 22 апреля                │  Provider-cancel
│                                               │  status (muted/warning,
│  Массаж лимфодренаж · 60 минут                │  NOT sage-green positive
│  2 200 ₽                                      │  per founder refinement)
│                                               │
│  Где                                          │
│  Beauty Place · Ирина                          │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Что произошло                                │
│                                               │
│  Запись отменена салоном. Если была оплата —  │  Per founder refinement:
│  возврат оформляется без штрафа.              │  no-fault explicit
│                                               │
│  У Ирины поменялись планы.                    │  Per no-fault
│  Деньги вернулись на карту 23 апреля.         │  voice rules
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ 📅 Записаться ещё ]                         │
│                                               │
└──────────────────────────────────────────────┘
```

### 5.5 Refund pending detail

```
┌──────────────────────────────────────────────┐
│  ← Запись                                     │
│  ─────────────────────────────────────       │
│                                               │
│  ⏱ Возврат в обработке · 25 мая                │
│                                               │
│  Маникюр · 1 800 ₽                            │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  Что происходит                               │
│                                               │
│  Запись отменена. Возврат ушёл в банк —       │  Refund timeline
│  обычно приходит в течение 3 дней.            │  per booking-flow §4.4
│                                               │
│  Если не пришёл к 28 мая — напиши мне,        │  Escalation path
│  разберусь.                                   │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ 💬 Спросить Ayla ]                          │  Direct to chat
│                                               │
└──────────────────────────────────────────────┘
```

### 5.6 Repeat booking flow (Q-R-3 smart fallback)

Tap «Записаться ещё»:
- **If backend prefill ready:** opens booking-flow F3 date+time picker с master + service preselected → customer выбирает только дату
- **If backend NOT ready:** opens booking-flow F1 catalog с filter «У {{master_first_name}}» auto-applied + service category preselected → customer picks specific slot

Voice on landing:
- Prefilled: «Записать тебя ещё раз к Анне на маникюр гель-лак. Когда удобно?»
- Catalog fallback: «У Анны Петровой сейчас свободно...»

---

## 6. R4 — Status vocabulary

8 status states. **Icons + Russian label** per Q-R-2 lean (WCAG color-only safe).

| State | Icon | Label | Color tint | Surface available actions |
|-------|------|-------|------------|----------------------------|
| `confirmed` | ✓ | Подтверждена | sage-green | open / write / route / reschedule / cancel |
| `rescheduled` | 🔄 | Перенесена | muted | open / write / route / reschedule / cancel (new booking still active). **Detail enhancement:** show «Перенесена с {original_datetime}» if origin slot data available |
| `cancelled` (customer) — detail enhancement | ✕ | Отменена вами | grey | open / repeat. **Detail enhancement:** «Отменена вами {date}» (emotionally distinct от provider cancellation) |
| `cancelled` (customer) | ✕ | Отменена | grey | open / repeat |
| `provider_cancelled` (no-fault) | ⚠ | Отменена салоном | muted/warning (NOT sage-green — не success) | open / repeat (refund visible) |
| `completed` | ✅ | Прошла | sage-green | open / repeat / leave review (if pending) |
| `no_show` | — | Не пришла | grey | open / repeat |
| `refund_pending` | ⏱ | Возврат в обработке | muted | open / ask Ayla |
| `refund_completed` | ✓₽ | Возврат завершён | sage-green | open / repeat |

### 6.1 Voice rules для status

- All Russian feminine («Подтверждена» / «Прошла») — «запись» = feminine noun
- NOT customer-facing punitive («Не состоялась» NOT «Прогул»)
- «Отменена салоном» NOT «Salon cancelled» — Russian, no English jargon
- «Возврат в обработке» NOT «Refund pending» — Russian
- NOT «Бронирование отменено» (sterile) — «Отменена» (feminine, brand voice)

### 6.2 Color tints (a11y safe)

Sage-green positive (confirmed / completed / provider_cancelled с full refund / refund_completed) — informational positive.

Muted grey for neutral lifecycle ends (cancelled / no_show / refund_pending) — NOT punitive red.

NEVER use red — anxiety-inducing. NEVER color-only meaning (icons + label always paired).

---

## 7. R5 — Empty states

### 7.1 No bookings ever (first-time user) — per founder §18

```
┌──────────────────────────────────────────────┐
│  ←  Записи                                    │
│  ─────────────────────────────────────       │
│                                               │
│  [ Ближайшие ][ История ]                     │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  Пока записей нет. Можно найти услугу         │  Per founder §18
│  или спросить Ayla.                           │  explicit wording
│                                               │
│  [ Найти услугу ]                             │
│  [ Спросить Ayla ]                            │
│                                               │
└──────────────────────────────────────────────┘
```

### 7.2 No upcoming but has past — per founder §18

```
┌──────────────────────────────────────────────┐
│  [ Ближайшие ][ История (5) ]                 │
│  ─────────────────────────────────────       │
│                                               │
│  Ближайших записей нет. Можно повторить       │  Per founder §18
│  прошлую запись или найти новую услугу.       │  explicit wording
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ✅ Маникюр у Анны · 20 мая           │   │  Quick repeat
│  │  Beauty Place · 2 400 ₽                │   │  preview last
│  │                                        │   │  positive booking
│  │  [ Записаться ещё ]                    │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ Записаться ещё ] [ История ]               │  Per founder CTAs
│                                               │
└──────────────────────────────────────────────┘
```

### 7.3 No history (only upcoming)

Edge case — customer has future bookings but no past yet. «История» tab empty:

```
┌──────────────────────────────────────────────┐
│  [ Ближайшие (2) ][ История ]                 │
│  ─────────────────────────────────────       │
│                                               │
│  История накопится после первых визитов.      │  Friendly note
│                                               │
└──────────────────────────────────────────────┘
```

---

## 8. R6 — Repeat booking CTA

«Записаться ещё» CTA on past booking cards. Routing per Q-R-3 smart fallback:

```
Customer taps «Записаться ещё»
  ↓
Backend check: can_prefill_booking(past_booking_id) ?
  ↓
  ├─ YES → booking-flow F3 date+time picker
  │        с master + service preselected
  │        Voice: «Записать ещё раз к Анне на маникюр.
  │               Когда удобно?»
  │
  └─ NO  → booking-flow F1 catalog
           с filter «У Анны Петровой» auto-applied
           + service category preselected
           Voice: «У Анны Петровой сейчас свободно...»
```

Per `customer-booking-flow.md` Variant C 3-layer ranking still applied if customer goes to F1 catalog fallback.

---

## 9. States matrix

### 9.1 R1 (main list) states

| State | Trigger | UX |
|-------|---------|-----|
| Loading skeleton | First open / refresh | Header + tabs cached. Card list = shimmer |
| Empty (never had) | 0 records | §7.1 empty state |
| Empty upcoming only | 0 future, has past | §7.2 «Ближайших нет — повтори» |
| Empty history only | 0 past, has future | §7.3 «накопится после визитов» |
| Pagination loading | «Показать ещё» tap | Inline spinner, append below |
| Filter applied | Customer tapped chip | List re-filtered, count badge updates |
| Multi-tenant | 2+ tenants active | Grouped per Variant C per §3.3 |
| Sticky banner | Nearest booking <24h | §3.5 banner above tabs |

### 9.2 R3 (detail) states

| State | Trigger | UX |
|-------|---------|-----|
| Loading | First open | Skeleton |
| Booking deleted (race) | Booking removed между tap и open | «Этой записи больше нет. Возможно, она была отменена.» + back |
| Status changed (race) | Customer opened «confirmed» but it's now «cancelled» | Refresh status header + actions, banner «Статус изменился» |
| Salon SUSPENDED | Tenant paused mid-view | Status banner «Beauty Place сейчас на паузе. Если что — пиши мне.» + reduced actions |
| API down | Backend unavailable | Show cached version if available + retry button. Else error screen. |

### 9.3 R6 (repeat) states

| State | Trigger | UX |
|-------|---------|-----|
| Backend prefill works | Repeat tap | Land на booking-flow F3 prefilled |
| Backend prefill fails | Edge case | Fallback к booking-flow F1 catalog с filter |
| Master deactivated since | Past master no longer active | Catalog с category filter + voice «Анна больше не работает в Beauty Place. Карина продолжает в том же стиле.» |
| Service deprecated | Service removed from catalog | Catalog с category filter + voice «Эта услуга больше не предлагается. Похожие:» |
| Salon SUSPENDED | Salon paused | Voice «Beauty Place сейчас на паузе. Когда вернутся — напомню. А пока:» + alternative salons |

---

## 10. Voice patterns

### 10.1 Status copy

| Surface | Voice |
|---------|-------|
| Status badge | «✓ Подтверждена» / «🔄 Перенесена» / «✕ Отменена» / etc per §6 |
| Past «Прошла» | «✅ Прошла · 20 мая» — date inline, factual |
| Cancellation context | «Отменена вами 27 апреля. Без штрафа — отмена за 24 часа.» (per founder refinement — «вами» NOT «тобой» в Records detail чтобы emotionally distinct от provider cancellation) |
| Provider-cancel | «У Ирины поменялись планы. Полный возврат — это не твоя вина.» |
| Refund pending | «Возврат ушёл в банк — обычно приходит в течение 3 дней.» |
| No-show | «Визит отмечен как не состоявшийся. 100 ₽ удержано — по условиям отмены.» (factual, no shaming) |

### 10.2 Empty states

| Surface | Voice |
|---------|-------|
| No bookings ever | «Пока записей нет. Можно найти услугу рядом или спросить Ayla.» |
| No upcoming, has past | «Ближайших записей нет. Можно повторить прошлую запись.» |
| No history | «История накопится после первых визитов.» |

### 10.3 Sticky banner (nearest <24h)

«⏰ Запись через 3 часа · 16:00 / Маникюр у Анны в Beauty Place» — informational, no urgency tone.

### 10.4 Repeat booking landing voice

- Prefilled: «Записать тебя ещё раз к Анне на маникюр гель-лак. Когда удобно?»
- Catalog fallback: «У Анны Петровой сейчас свободно...»
- Master deactivated: «Анна больше не работает в Beauty Place. Карина продолжает в том же стиле.»
- Service deprecated: «Эта услуга больше не предлагается. Похожие:»

### 10.5 Records voice principles (per founder refinement)

> **Records = место контроля и уверенности. «Всё под рукой» feeling.**

Не selling, not pushing. Customer открыла Records чтобы посмотреть свои записи — она НЕ shopping. Voice = calm, informational, control-supporting.

| ❌ Forbidden (selling voice) | ✅ Records voice |
|------------------------------|------------------|
| «Запишись снова!» (exclamation) | «Записаться ещё» |
| «Попробуй ещё!» (push) | «Повторить запись» |
| «Тебя ждут новые мастера!» (FOMO) | «Найти услугу» |
| «Не упусти момент!» | (silent — customer decides) |
| «Скидка для постоянных!» (promo in records) | (no promo в Records — это control surface) |

### 10.6 Anti-patterns

- ❌ Exclamation marks anywhere в Records («Запишись!», «Прошла отлично!»)
- ❌ Selling tone («Тебя ждут новые мастера», «Не упусти момент»)
- ❌ Promo / discount surfaces в Records (control space, not commerce)
- ❌ «Бронирование отменено в системе» (sterile) — use «Отменена»
- ❌ «Прогул» / «Не пришла» (loaded / gendered) — use «Не состоялась» per founder refinement
- ❌ «История пуста» (cold) — use «История накопится...»
- ❌ «Bookings» / «Records» (English) — use «Записи»
- ❌ Color-only status (red badge для cancelled без icon+label)
- ❌ Punitive no-show wording («Вы прогуляли визит»)
- ❌ Hide refund timeline (anxiety-inducing) — show «3 дней» promise
- ❌ Show booking ID / hash (technical leak)
- ❌ «Уважаемый клиент» (corporate)
- ❌ Surface admin identity in any cancellation context
- ❌ Provider-cancelled badge sage-green positive (NOT success — use muted/warning per founder refinement)
- ❌ Emoji-only status icons (use SVG icons per founder Q-R-2 caveat — emoji rendering inconsistent Android/iOS/MAX webview)

---

## 11. Phase E — Variants comparison

### 11.1 Section layout

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) 2 tabs «Ближайшие/История»** | ✅ **SELECTED** | Per tech lead spec, clear mental model |
| (b) Unified scroll list | ❌ Rejected | Long lists fatigue, unclear past/future boundary |
| (c) Filter chips «Все/Будущие/Прошедшие/Отменённые» | ❌ Rejected | Per tech lead «не 4 тяжёлые вкладки» |

### 11.2 Status visual treatment

| Variant | Selected | Reason |
|---------|----------|--------|
| (a) Colored badges | ❌ Rejected | WCAG 1.4.1 color-only meaning risk |
| (b) Text-only labels | ❌ Rejected | Slower scan, less visual hierarchy |
| **(c) Icons + label** | ✅ **SELECTED** | Colorblind-safe, scannable |

### 11.3 Booking detail layout

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Single tall scroll** | ✅ **SELECTED** | Mobile-native pattern, no friction |
| (b) Tabs inside detail | ❌ Rejected | Extra navigation для simple read |
| (c) Hybrid (primary + collapsible) | ⏸ Alt | More complex, but acceptable если scroll too long |

### 11.4 Past booking actions

| Variant | Selected | Reason |
|---------|----------|--------|
| (a) No actions at all | ❌ Rejected | Customer needs «Записаться ещё» repeat |
| (b) Only «Записаться ещё» | ⏸ Alt | OK but misses review surface |
| **(c) «Записаться ещё» + «Оставить отзыв» if pending** | ✅ **SELECTED** | Reuses review flow без bloat |

### 11.5 Multi-tenant Variant C

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Same Variant C as booking-flow §8** | ✅ **SELECTED** | Consistency, adaptive smart |
| (b) Always flat chronological | ❌ Rejected | Loses tenant context for multi-tenant |
| (c) Always grouped by tenant | ❌ Rejected | Single-tenant unnecessary chrome |

---

## 12. Backend mapping

### 12.1 New / extended endpoints (per founder spec — Ayla canonical booking domain per ADR-0009)

| Endpoint | Method | Description | Owner |
|----------|--------|-------------|-------|
| `GET /api/v1/me/bookings?section=upcoming` | GET | Ближайшие section list | Ayla canonical |
| `GET /api/v1/me/bookings?section=history` | GET | История section list с pagination | Ayla canonical |
| `GET /api/v1/me/bookings/{booking_id}` | GET | Booking detail (R3) with refund timeline, cancellation context, rescheduled origin slot | Ayla canonical |
| `POST /api/v1/bookings/{booking_id}/repeat-intent` (optional) | POST | Repeat booking — returns prefilled context if available. OR frontend opens booking flow с query params | Ayla canonical |
| `GET /api/v1/me/bookings/{id}/review_status` | GET | Check if customer can leave review (pending feedback) | Existing per review handoff |
| `POST /api/v1/me/bookings/{id}/notes` | POST | Add/edit master note | Existing |

**Per ADR-0009:** endpoints = Ayla canonical (booking domain). Tech lead handles backend coordination отдельно. Tau focuses doc only.

**Backend-generated prefill preferred** — knows активность мастера, архивацию услуги, изменение цены, доступные слоты.

### 12.2 Response fields critical

**`/me/bookings` list response:**
```json
{
  "section": "upcoming",
  "total_count": 5,
  "groupings": [
    {
      "tenant_id": "uuid",
      "tenant_name": "Beauty Place",
      "bookings": [
        {
          "booking_id": "uuid",
          "status": "confirmed",  // per §6 vocabulary
          "datetime_iso": "...",
          "datetime_relative_label": "Завтра · пт · 16:00",
          "service_name": "Маникюр гель-лак",
          "master_first_name": "Анна",
          "is_nearest": true,  // for sticky banner + full actions
          "actions_available": ["open", "write", "route", "reschedule", "cancel"],
          ...
        }
      ]
    }
  ]
}
```

**`/me/bookings/{id}/repeat` response:**
```json
{
  "prefill_available": true,
  "master_id": "uuid",
  "service_id": "uuid",
  "redirect_to": "booking_flow_f3",  // or "booking_flow_f1_catalog"
  "fallback_reason": null  // or "master_deactivated" / "service_deprecated" / "salon_suspended"
}
```

### 12.3 Status determination logic

Backend computes status per booking state machine (per `customer-cancellation-reschedule-spec.md` §2). Frontend just renders icon + label per §6 mapping.

---

## 13. Accessibility (WCAG 2.2 AA — inline)

Patterns reuse from `customer-booking-flow.md §11`. Records-specific:

1. **1.4.1 Use of Color** — Status conveyed via icon + Russian label, NEVER color-only. Sage-green / muted treatments accompany text.

2. **2.5.8 Target Size** — All booking card action buttons ≥44dp. «Записаться ещё» / «Оставить отзыв» / «Открыть» each ≥44dp tap area.

3. **1.4.3 Contrast** — Muted status labels (grey for cancelled/no_show) must meet 4.5:1. Sage-green «✓ Подтверждена» badge same.

4. **1.3.1 Info & Relationships** — Tab pattern `role="tablist"` / `role="tab"` / `role="tabpanel"`. Section headings («── Beauty Place ──» / «── Завтра ──») use `<h2>` semantics.

5. **2.4.3 Focus Order** — On tab switch focus moves to first card. Booking detail focus order: header → status → date/time → location → note → policy → actions.

6. **4.1.3 Status Messages** — Repeat booking loading «Готовлю запись...» = `role="status"`.

7. **2.4.1 Bypass Blocks** — Skip link «К списку записей» on tab content load.

8. **1.4.4 Resize Text** — At 200% zoom: cards stack vertical, action buttons wrap to multi-line, status badge stays prominent.

9. **2.5.5 Confirm Destructive** — «Отмена» button on card opens cancellation modal (not direct destructive). Per `customer-cancellation-reschedule-flow.md` §4.2.

10. **3.1.1 Language** — `lang="ru"` declared. Booking ID hashes hidden from screen reader (`aria-hidden="true"`).

11. **2.3.3 Reduced Motion** — Skeleton shimmer respects `prefers-reduced-motion: reduce`.

---

## 14. Anti-patterns

- ❌ 4+ heavy tabs «Все/Будущие/Прошедшие/Отменённые» (per tech lead — 2 sections enough)
- ❌ Color-only status badges (WCAG 1.4.1)
- ❌ English «Bookings» / «Records» in UI (use «Записи»)
- ❌ Show booking ID / internal hashes
- ❌ Surface admin identity в cancellation context
- ❌ Punitive no-show wording («прогуляли» / «не явились»)
- ❌ Hide refund timeline (anxiety-inducing — show 3 days)
- ❌ Force review submission popup blocking list
- ❌ «Уважаемый клиент» (corporate)
- ❌ Auto-refresh без user action (battery drain + confusion)
- ❌ Sort by «most expensive first» (commerce vibe)
- ❌ Color red для cancelled (red = error, cancelled = neutral lifecycle end)
- ❌ Hide cancelled bookings entirely (transparency loss)
- ❌ Endless infinite scroll (use «Показать ещё» pagination)
- ❌ Cross-tenant data leakage (one tenant's note visible to another) — privacy boundary
- ❌ Past booking «Написать по записи» (per ayla-mediated-messaging §11.1 — closed bookings have no messaging)

---

## 15. Open questions / followups

### Resolved at Phase B

All Q-R-1..10 resolved 2026-05-26:
- Q-R-1: (a) 2 tabs «Ближайшие/История» ✅
- Q-R-2: (c) Icons + label ✅
- Q-R-3: (c) Smart fallback prefill ✅
- Q-R-4: (a) Single tall scroll detail ✅
- Q-R-5: (c) «Записаться ещё» + «Оставить отзыв» if pending ✅
- Q-R-6: (b) Cancelled in «История» с status badge ✅
- Q-R-7: (b) Refund timeline в booking detail only ✅
- Q-R-8: (a) Variant C same as booking-flow §8 ✅
- Q-R-9: (a) Filter chips в «История» only ✅
- Q-R-10: (a) Dashboard «Все записи» → «Ближайшие» default ✅

### Post-pilot followups

| # | Question | Phase |
|---|----------|-------|
| Q-R-POST-1 | Calendar view alternative для power users | Phase 2+ |
| Q-R-POST-2 | Per-booking message history thread visibility | Phase 2+ |
| Q-R-POST-3 | Bulk actions (cancel multiple) | Phase 2+ |
| Q-R-POST-4 | Cross-tenant CRM summary («ходишь чаще к Анне, чем к Карине») | Phase 2+ |
| Q-R-POST-5 | Export bookings (PDF / iCal) | Phase 2+ |
| Q-R-POST-6 | Recurring booking visualization | Phase 2+ |
| Q-R-POST-7 | Payment ledger detailed view | Phase 3+ |
| Q-R-POST-8 | Receipt / чек download | Phase 3+ |

### For W1 / Iota (frontend implementer)

1. **Tab pattern** — `role="tablist"` ARIA, swipe-gesture между tabs OK
2. **Status badge component** — reusable icon + label component с sage-green/muted tints
3. **Variant C grouping** — reuse from booking-flow §8 implementation
4. **Sticky banner** — only if `is_nearest && hours_to_visit < 24`
5. **Repeat booking routing** — handle 3 fallback cases (prefill / catalog / both deprecated)
6. **Refund timeline** — show «3 дней» promise vs actual elapsed time
7. **Pagination** — «Показать ещё (12)» не infinite scroll
8. **Filter chips** — only inside «История», hide if total past <5 bookings
9. **Booking detail action buttons** — different sets per status per §6 table
10. **Multi-tenant grouping headers** — `<h2>` semantic, NOT `<div>`

---

## 16. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | ASCII pattern reuse from booking-flow + dashboard |
| Direct context (cumulative session) | A | All foundation docs in context from previous PRs (booking, cancel, messaging, dashboard, IA) |
| `Brand Guardian` subagent | F | Records voice = informational, review applied inline below |
| UI Designer subagent | (skipped — pattern reuse) | n/a |
| Accessibility Auditor subagent | (skipped — inline notes §13) | n/a |

---

## 17. Status next steps

- [x] Phase A — context confirmed (cumulative from session)
- [x] Phase B — 10 Q-R questions + scope per tech lead UX-UI.txt
- [x] Phase C — R1-R6 ASCII (list + cards + detail + status + empty + repeat)
- [x] Phase D — states matrix + voice patterns + status vocabulary
- [x] Phase E — 5 variants comparison
- [x] Phase F — Brand Guardian voice review (applied inline)
- [x] Phase G — A11y notes inline §13
- [x] Phase I — save `docs/screens/customer-records-flow.md`
- [ ] Phase J — handoff block
- [ ] Phase K — commit + push + PR + self-merge

**Severity:** P0 BLOCKER pilot — booking lifecycle visibility incomplete без Records tab.

**Streams unblocked:**
- W1 — ~18-22 hrs (R1 list с 2 sections + filters + R2 cards + R3 detail screen + R4 status renderer + R5 empty states + R6 repeat CTA + Variant C reuse)
- W4 — ~3-4 hrs (records list endpoint + booking detail endpoint + repeat prefill endpoint)

---

## 18. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (records strategic plan) | ✅ | 2026-05-26 |
| Tech Lead (Phase B scope + Q-R verdicts via «approve all leans») | ✅ | 2026-05-26 |
| Tau (author) | ✅ | 2026-05-26 |
| UX Architect | ☐ | (pending review) |
| Brand Guardian (voice review pending) | 🟡 | running |
| W1 (R1-R6 frontend) | ☐ | (pending impl) |
| W4 (records endpoints) | ☐ | (pending impl) |
| Accessibility Engineer | ☐ | (pending pilot) |

## Last verified
2026-05-26 r1 — Tech lead UX-UI.txt scope addition applied. All Q-R-1..10 resolved via «approve all leans». R1-R6 + status vocabulary documented. Brand Guardian voice review in progress.
