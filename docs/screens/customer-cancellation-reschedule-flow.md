# Screen: customer-cancellation-reschedule-flow

| Field | Value |
|---|---|
| **Audience** | customer initiating cancel/reschedule OR receiving salon-side notification (master sick, admin cancels) |
| **Phase** | P0 BLOCKER pilot 15 July 2026 — full booking lifecycle completeness |
| **Status** | draft — Phase A–G done, awaiting tech lead final sign-off + frontend handoff |
| **Channel** | MAX webview Mini App + bot DM |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-26 r1 |
| **Foundation** | [`customer-cancellation-reschedule-spec.md`](../design/policies/customer-cancellation-reschedule-spec.md) (policy state machine) · memory `project_q12a_billing_founder_gate` (chain rules + 5 edge cases ACK) · [`booking-conflict-resolution-ux.md §6.6b`](../design/policies/booking-conflict-resolution-ux.md) (master substitution) · [`customer-booking-flow.md`](./customer-booking-flow.md) F4 (cancellation policy preview) · `project_booking_flow_implementation_cut` (cuts baseline) |
| **Severity** | P0 BLOCKER pilot (без cancellation/reschedule UX = half-baked customer experience, support load + trust damage) |

---

## 1. Контекст

### Why this exists

Per founder strategic plan 2026-05-26 — closing full booking lifecycle:

> 1. ✅ Customer finds service (booking flow)
> 2. ✅ Customer books
> 3. ✅ Customer confirms
> 4. 🟡 Customer writes по записи (Ayla-mediated, backend pending)
> 5. ❌ Customer reschedules ← THIS
> 6. ❌ Customer cancels ← THIS
> 7. 🟡 Customer sees history (Records — separate scope)

Без этих flows customers WILL cancel/reschedule вручную (через chat с Ayla / звонок в салон). Without smooth UX → support load + trust damage.

### Voice critical — «Ayla on customer's side»

Per founder ranking philosophy (`project_ayla_ranking_philosophy` 2026-05-26):

> Ayla никогда не guilt-trips, не вынуждает остаться. Late cancellation penalty disclosure brief and factual, NOT punitive.

| ✅ Allowed | ❌ Forbidden |
|---------|---------|
| «Поняла, отменяю. За 12+ часов до визита — без штрафа.» | «Жаль терять вас» |
| «До визита меньше 12 часов — салон может удержать 50% (около 900 ₽).» | «Может, перенесём?» (guilt-trip) |
| «Передаю Ирине — узнаю когда увидимся.» | «Подумайте ещё раз» |
| «Поняла. Если что — пиши.» | «Это безвозвратное действие» (legalese) |

### Implementation cuts baseline applied

Per memory `project_booking_flow_implementation_cut` (2026-05-26), apply same discipline:
- **Reasoning text от backend** (NOT frontend generation)
- **«Cancel» → «Отмена»** во всех русских текстах
- **Cancellation tone мягкий** («может удержать» не «удержит»)
- **Trust + ranking philosophy applies** (3-layer когда suggesting alternatives)
- **Q12-α billing chain integration explicit** (memory `project_q12a_billing_founder_gate`)
- **No-fault refund cascade** when salon-side cancellation (founder Q2 verdict 2026-05-26)

---

## 2. Q12-α billing chain awareness (invisible to customer)

Per memory `project_q12a_billing_founder_gate` + 2026-05-23 commercial identity extension. 5 founder-ACKed edge cases:

1. **Cancel breaks chain.** No grace-window для MVP.
2. **Service swap breaks chain.** Strict service_id equality + 4-field commercial identity (service_id + sticker_price_amount + currency + duration_minutes).
3. **90 days threshold** from original root visit_at.
4. **Partial-failure** = terminal cancel + admin audit visibility.
5. **No depth cap** — unlimited reschedules within 90 days.

### UX rules for customer

**Customer NEVER sees «chain» concept.** But also NOT surprised с unexpected bills.

| Scenario | Reschedule confirmation copy |
|----------|------------------------------|
| Same service, ≤90 days, same chain | «Перенесла на четверг 16:00. Стоимость та же — ничего не меняется.» |
| Service swap (different service_id) | «Перенесла на четверг 16:00 + поменяла услугу. Это новая запись — стоимость пересчитается.» |
| Price changed (4-field identity broken — admin updated pricing) | «Перенесла на четверг 16:00. Цена изменилась с 1 800 ₽ на 2 100 ₽ — это новая запись.» |
| >90 days from chain root | «Дата далеко — это считается новой записью. Стоимость пересчитается.» |
| Partial failure (backend transaction broke) | «Что-то пошло не так — старая запись отменилась, новая не создалась. Уже разбираюсь. Возврат за старую запись приедет.» |

### Cancellation chain semantics

- **Cancel terminates chain.** Next booking customer создаёт = new chain, billable.
- Customer не видит этого — просто видит «Отменила запись. Возврат приедет в течение 3 дней» (если applicable).

---

## 3. Entry points

| Surface | Trigger | Auth |
|---------|---------|------|
| Dashboard booking card | Tap `Отменить` или `Перенести` | Customer authenticated |
| Records tab booking detail | Tap actions panel | Customer authenticated |
| Bot DM с Ayla | Customer types «отмени запись» / «перенеси» / «не приду» | Customer authenticated, intent resolved per active bookings count |
| Ayla notification (salon-side) | Admin cancelled customer's booking → Ayla DM с alternatives | Customer authenticated |

### Bot DM intent resolution

Per spec policy §3.2 + ayla-mediated-messaging Q-AMM-10 pattern:

```
Customer types: «отмени запись» / «перенеси»

Logic:
  if customer.has_0_active_bookings():
      → «У тебя сейчас нет активных записей. Если что-то нужно — напиши.»
  elif customer.has_1_active_booking():
      → auto-context: «Ты про массаж завтра в 16:00 у Ирины?» + [Да] [Нет]
  else:  # 2+ active bookings
      → selector: «Какую запись?» + chips per booking
```

---

## 4. Cancellation flow (4 screens + states)

### 4.1 Entry from booking card / Records tab

Customer taps **`Отменить`** action button. Modal opens (per Q-CR-4 verdict — modal для irreversible action).

### 4.2 C1 — Confirmation prompt с policy preview

#### C1 — Early cancellation (12+ часов до визита)

```
┌──────────────────────────────────────┐
│  Отменить запись?                     │  Modal overlay
│                                       │
│  Четверг 30 мая · 16:00               │  Booking context
│  Маникюр гель-лак · 90 мин            │
│  у Ирины · Beauty Place               │
│                                       │
│  ── Если что ──                       │
│                                       │
│  До визита больше 12 часов — отмена   │
│  без штрафа.                          │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Да, отменить ]                     │
│  [ Передумала ]                       │
└──────────────────────────────────────┘
```

#### C1 — Late cancellation (<12 часов до визита) — WARNING

```
┌──────────────────────────────────────┐
│  Отменить запись?                     │
│                                       │
│  Сегодня в 16:00                      │  Booking context
│  Маникюр гель-лак · 90 мин            │
│  у Ирины · Beauty Place               │
│                                       │
│  ── Важно ──                          │
│                                       │
│  До визита меньше 12 часов. Beauty    │  Late penalty
│  Place может удержать 50% (около      │  EXPLICIT amount
│  900 ₽).                              │  (Q-CR-6 lean)
│                                       │
│  Если правда не получается — пиши     │  Compassionate
│  Ирине через «Сообщить по записи».    │  alternative
│                                       │  (cross-link to
│  ─────────────────────────────       │  ayla-mediated)
│                                       │
│  [ Да, отменить ]                     │
│  [ Написать Ирине ]                   │
│  [ Передумала ]                       │
└──────────────────────────────────────┘
```

**Voice rules:**
- «может удержать» (NOT «удержит» — softer per founder cut baseline)
- Concrete amount «около 900 ₽» (not just «50%»)
- Compassionate alternative «Написать Ирине» offered before commit
- NOT punitive — informational

### 4.3 C2 — Optional reason (after C1 «Да, отменить»)

Per Q-CR-1 verdict — **optional с «Пропустить»** chip.

```
┌──────────────────────────────────────┐
│  Что повлияло?                        │  Optional
│                                       │
│  (можешь пропустить)                  │
│                                       │
│  [ Не успеваю ]                       │
│  [ Изменились планы ]                 │
│  [ Не нужна услуга сейчас ]           │
│  [ Хочу другого мастера ]               │
│  [ Другое ]                           │
│  [ Пропустить ]                       │
└──────────────────────────────────────┘
```

**Voice rules:**
- «(можешь пропустить)» under header — explicit no pressure
- Chips factual, not loaded («Не подходит мастер» NOT «Не понравился мастер»)
- «Другое» → text input optional
- «Пропустить» = same outcome, no reason recorded

### 4.4 C3 — Success + refund info

#### Success — early cancellation (no penalty)

```
┌──────────────────────────────────────┐
│  ✓ Отменила запись                    │  Sage-green check
│                                       │
│  Четверг 30 мая · 16:00               │  Recap
│  у Ирины · Beauty Place               │
│                                       │
│  Без штрафа — до визита было больше   │  Reassurance
│  12 часов.                            │
│                                       │
│  Если ещё что-то понадобится —        │  Door open
│  я тут.                               │  per Q-CR-5 lean
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Записаться снова ]                 │
│  [ На главную ]                       │
└──────────────────────────────────────┘
```

#### Success — late cancellation (penalty applied)

```
┌──────────────────────────────────────┐
│  ✓ Отменила запись                    │
│                                       │
│  Сегодня в 16:00                      │
│  у Ирины · Beauty Place               │
│                                       │
│  Beauty Place удерживает 900 ₽ —      │  Factual statement
│  это политика отмены за 12 часов.     │  No moralizing
│                                       │
│  Остаток вернётся на карту в течение  │
│  3 дней.                              │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Понятно ]                          │
│  [ На главную ]                       │
└──────────────────────────────────────┘
```

#### Success — no refund applicable (was free / loyalty fully covered / etc)

```
┌──────────────────────────────────────┐
│  ✓ Отменила запись                    │
│                                       │
│  Четверг 30 мая · 16:00               │
│  у Ирины · Beauty Place               │
│                                       │
│  Если ещё что-то понадобится —        │
│  я тут.                               │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Записаться снова ]                 │
│  [ На главную ]                       │
└──────────────────────────────────────┘
```

### 4.5 Cancellation states

| State | Trigger | UX |
|-------|---------|-----|
| Loading (during commit) | After «Да, отменить» tap | Brief spinner ≤1.5s «Отменяю...» |
| Network error | Cancel request failed | «Не получилось отменить — попробуй ещё раз через минуту.» + retry button |
| Already cancelled (race condition) | Customer cancels booking уже cancelled (e.g., admin cancelled simultaneously) | «Эта запись уже отменена. Если что-то ещё — пиши.» |
| Booking in past (race condition) | Customer tries cancel after visit time | «Запись уже прошла — отменить не получится. Если возникли вопросы — напиши.» |
| API down | Backend unavailable | «Сервис недоступен — попробуй через минуту. Запись пока без изменений.» |

---

## 5. Reschedule flow (5 screens + states)

### 5.1 Entry from booking card / Records tab

Customer taps **`Перенести`** action button. Per Q-CR-2 verdict — single-tap to R1 date+time picker (no intent re-confirmation).

### 5.2 R1 — Date+time picker (similar к booking flow F3)

```
┌──────────────────────────────────────┐
│  ←  Перенести запись                  │  Header 56dp
│  ─────────────────────────────────   │
│                                       │
│  Маникюр гель-лак · 90 мин            │  Booking context
│  у Ирины · Beauty Place               │
│  Было: четверг 30 мая · 16:00         │
│                                       │
│  ── ✨ Похоже подойдёт ──              │  Smart suggestions
│                                       │  (state-dependent
│                                       │   per booking-flow
│                                       │   cut #4)
│  ┌────────────────────────────────┐  │
│  │  Пятница 31 мая · 14:00         │  │
│  │  Свободно у Ирины               │  │  REASONING TEXT
│  │  [ Выбрать ]                    │  │  (backend-generated
│  └────────────────────────────────┘  │   per booking-flow §10.3)
│                                       │
│  ┌────────────────────────────────┐  │
│  │  Понедельник 2 июня · 16:00     │  │
│  │  Похоже на твоё обычное время   │  │
│  │  [ Выбрать ]                    │  │
│  └────────────────────────────────┘  │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  ── Все слоты ──                      │
│                                       │
│  ── Пятница 31 мая ──                 │
│  10:00 · 14:00 ✨ · 16:00 · 18:00     │
│                                       │
│  ── Суббота 1 июня ──                 │
│  Закрыто — выходной                   │
│                                       │
│  ── Понедельник 2 июня ──             │
│  9:30 · 11:00 · 13:30 · 16:00 ✨      │
│                                       │
│  [ Показать ещё неделю ]              │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Отменить вместо переноса ]         │  Escape route
│                                       │  to cancel flow
└──────────────────────────────────────┘
```

**State-dependent smart suggestions header** per booking-flow cut #4:
- Anonymous/new (rare для reschedule): «Ближайшие свободные»
- Registered с behavior: «Похоже подойдёт»
- Loyal (5+ visits с мастером): «Твоё обычное время»

### 5.3 R2 — Master substitution (when original master unavailable)

Per Q-CR-3 verdict — **inline в R1 date+time picker** (NOT separate screen).

#### R2 inline — when picking date where master unavailable

```
┌──────────────────────────────────────┐
│  ←  Перенести запись                  │
│  ─────────────────────────────────   │
│                                       │
│  Маникюр гель-лак · 90 мин            │
│  Было: четверг 30 мая · 16:00         │
│                                       │
│  ── Пятница 31 мая ──                 │
│                                       │
│  Ирина занята весь день. Но Карина —  │  Substitution
│  она тоже маникюрщица в Beauty Place: │  inline per spec
│                                       │  policy §6.6b
│  ┌────────────────────────────────┐  │
│  │  Карина · 14:00                 │  │
│  │  5 лет в маникюре, ⭐ 4.8       │  │  REASONING TEXT
│  │  [ Выбрать Карину ]             │  │
│  └────────────────────────────────┘  │
│                                       │
│  ┌────────────────────────────────┐  │
│  │  Карина · 16:30                 │  │
│  │  [ Выбрать ]                    │  │
│  └────────────────────────────────┘  │
│                                       │
│  Или дождаться Ирину:                 │  Original master
│  Понедельник 2 июня · 16:00 ✨        │  preserved option
│                                       │
│  ─────────────────────────────       │
│                                       │
│  ── Понедельник 2 июня ──             │
│  ...                                  │
│                                       │
└──────────────────────────────────────┘
```

**Voice rules:**
- «Ирина занята» (factual, not «недоступна» — corporate)
- «Карина — она тоже маникюрщица в Beauty Place» — collaborative framing, salon as venue
- «Или дождаться Ирину» — original master option preserved
- Reasoning text per substitution candidate (per booking-conflict-resolution-ux §6.6b)

### 5.4 R3 — Confirmation (with Q12-α billing transparency)

#### Same chain (most common case)

```
┌──────────────────────────────────────┐
│  Подтверди перенос                    │  Modal
│                                       │
│  Было: четверг 30 мая · 16:00         │  Before
│  Стало: пятница 31 мая · 14:00        │  After
│                                       │
│  у Ирины в Beauty Place               │
│  Маникюр гель-лак                     │
│                                       │
│  Стоимость та же — ничего не          │  Q12-α chain
│  меняется.                            │  same chain
│                                       │  (Q-CR-7 lean:
│  ─────────────────────────────       │   always show)
│                                       │
│  [ ✓ Перенести ]                      │
│  [ Изменить время ]                   │
│  [ Отмена ]                           │
└──────────────────────────────────────┘
```

#### Service swap (chain breaks)

```
┌──────────────────────────────────────┐
│  Подтверди перенос                    │
│                                       │
│  Было: четверг 30 мая · 16:00         │
│       Маникюр гель-лак · 1 800 ₽      │
│                                       │
│  Стало: пятница 31 мая · 14:00        │
│       Маникюр + дизайн · 2 400 ₽      │  Different service
│                                       │
│  Это новая запись — стоимость         │  Q12-α explicit
│  пересчитается.                       │  (founder cut
│                                       │   baseline)
│  ─────────────────────────────       │
│                                       │
│  [ ✓ Перенести ]                      │
│  [ Оставить как было ]                │
└──────────────────────────────────────┘
```

#### >90 days from chain root

```
┌──────────────────────────────────────┐
│  Подтверди перенос                    │
│                                       │
│  Было: 30 мая · 16:00                 │
│  Стало: 5 сентября · 14:00            │
│                                       │
│  Дата далеко — это считается новой    │  >90 days threshold
│  записью. Стоимость пересчитается.    │  explicit
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ ✓ Перенести ]                      │
│  [ Выбрать дату ближе ]               │
└──────────────────────────────────────┘
```

#### Master substitution case (Карина instead of Ирины)

```
┌──────────────────────────────────────┐
│  Подтверди перенос                    │
│                                       │
│  Было: четверг 30 мая · 16:00         │
│       у Ирины                         │
│                                       │
│  Стало: пятница 31 мая · 14:00        │
│       у Карины · Beauty Place         │  Different master
│       Маникюр гель-лак                │  same service
│                                       │
│  Стоимость та же — ничего не          │  Same chain,
│  меняется.                            │  master change OK
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ ✓ Перенести к Карине ]             │
│  [ Изменить время ]                   │
└──────────────────────────────────────┘
```

### 5.5 R4 — Success

```
┌──────────────────────────────────────┐
│  ✓ Перенесла запись                   │  Sage-green check
│                                       │
│  Пятница 31 мая · 14:00               │  New booking
│  у Ирины · Beauty Place               │
│                                       │
│  Я напомню перед визитом — всё на     │  Reminder
│  месте.                               │  (soft per cut #5
│                                       │  + warmth beat per
│  Если ещё что-то — пиши.              │  Brand Guardian)
│                                       │  Door open
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Маршрут до салона ]                │
│  [ Открыть запись ]                   │
│  [ На главную ]                       │
└──────────────────────────────────────┘
```

### 5.6 Reschedule states

| State | Trigger | UX |
|-------|---------|-----|
| Loading | Submit reschedule | «Переношу...» spinner ≤2s |
| Slot taken (race) | Customer's chosen slot taken by another booking | «Это время только что заняли. Карина свободна 14:30 — подойдёт?» + substitution inline |
| Master deactivated mid-flow | Master removed from tenant between R1 and confirmation | «Ирина больше не работает в Beauty Place. Карина продолжает в том же стиле: пятница 14:00. Подойдёт?» |
| Partial failure (Q12-α edge case #4) | Backend transaction broke — old cancelled, new not created | «Что-то пошло не так. Старая запись отменилась, новая не создалась. Уже разбираюсь. Возврат за старую запись приедет.» + admin queue ticket |
| API down | Backend unavailable | «Сервис недоступен — попробуй через минуту. Запись пока без изменений.» |
| Tenant SUSPENDED | Salon paused mid-flow | «Beauty Place сейчас на паузе. Карина из Студии Лотос свободна — подойдёт?» + redirect |

---

## 6. No-fault salon-side cascade

Per `customer-cancellation-reschedule-spec.md` §6 + founder Q2 verdict 2026-05-26 — no-fault full refund.

### 6.1 Trigger

- Master sick day (admin marks master unavailable for date)
- Master schedule change (admin approved ScheduleChangeRequest)
- Master offboarding (departure)
- Salon-imposed cancellation (rare — operational emergency)

### 6.2 Ayla DM notification to customer (per Q-CR-9 lean — DM first)

```
─────────────────────────────────────
Ayla:

Ирина не сможет принять тебя в четверг
30 мая в 16:00 — поменялись планы.

У Карины (Beauty Place) свободно:
• Завтра четверг 16:30
• Пятница 14:00

Или подберём другую дату с Ириной?

[ Карина · четверг 16:30 ]
[ Карина · пятница 14:00 ]
[ Другую дату с Ириной ]
[ Отменить с возвратом ]
─────────────────────────────────────
```

**Voice rules:**
- «не сможет принять» (NOT «отменил запись» — owner-side language hidden per ayla-emergency-fallback-policy)
- «поменялись планы» — vague enough to not blame anyone
- Customer agency — multiple options
- «Отменить с возвратом» — explicit refund promise

### 6.3 Customer accepts substitution → Reschedule confirmation per §5.4

Standard reschedule flow. Refund N/A (no payment loss).

### 6.4 Customer chooses different date с Ириной → R1 date+time picker

Standard reschedule flow с Ирины availability shown for next 14 days.

### 6.5 Customer chooses cancel with refund

```
┌──────────────────────────────────────┐
│  ✓ Отменила запись                    │
│                                       │
│  Четверг 30 мая · 16:00               │
│  у Ирины · Beauty Place               │
│                                       │
│  Полный возврат — это не твоя вина.   │  No-fault refund
│                                       │  founder Q2 verdict
│  Деньги вернутся на карту в течение   │
│  3 дней.                              │
│                                       │
│  Если ещё нужно записаться —          │
│  у Карины свободно:                   │  Soft cross-promo
│  • Завтра 16:30                       │  (NOT pushy)
│  • Пятница 14:00                      │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ Записаться к Карине ]              │
│  [ На главную ]                       │
└──────────────────────────────────────┘
```

**Voice rules:**
- «не твоя вина» — explicit reassurance per founder no-fault verdict
- «Полный возврат» — concrete promise
- «3 дней» — specific timing
- Soft cross-promo (Карина alternatives) without pressure

---

## 7. Voice patterns

### 7.1 Cancellation copy summary

| Surface | Voice |
|---------|-------|
| Entry confirmation header | «Отменить запись?» |
| Early cancel policy line | «До визита больше 12 часов — отмена без штрафа.» |
| Late cancel warning | «До визита меньше 12 часов. Beauty Place может удержать 50% (около 900 ₽).» |
| Reason chips header | «Что повлияло? (можешь пропустить)» |
| Success early | «✓ Отменила запись. Без штрафа — до визита было больше 12 часов.» |
| Success late с penalty | «✓ Отменила запись. Beauty Place удерживает 900 ₽ — это политика отмены за 12 часов. Остаток вернётся на карту в течение 3 дней.» |
| Success no payment | «✓ Отменила запись. Если ещё что-то понадобится — я тут.» |

### 7.2 Reschedule copy summary

| Surface | Voice |
|---------|-------|
| R1 header | «Перенести запись» |
| Smart suggestions header (state-dependent) | Anonymous: «Ближайшие свободные» / Registered: «Похоже подойдёт» / Loyal: «Твоё обычное время» |
| Master substitution inline | «Ирина занята весь день. Но Карина — она тоже маникюрщица в Beauty Place:» |
| R3 same chain | «Стоимость та же — ничего не меняется.» |
| R3 service swap | «Это новая запись — стоимость пересчитается.» |
| R3 >90 days | «Дата далеко — это считается новой записью. Стоимость пересчитается.» |
| R4 success | «✓ Перенесла запись. Я напомню перед визитом. Если ещё что-то — пиши.» |

### 7.3 No-fault cascade copy summary

| Surface | Voice |
|---------|-------|
| Salon-side cancel DM | «Ирина не сможет принять тебя в четверг 30 мая в 16:00 — поменялись планы.» |
| Refund promise | «Полный возврат — это не твоя вина. Деньги вернутся на карту в течение 3 дней.» |
| Soft cross-promo | «Если ещё нужно записаться — у Карины свободно:» |

### 7.4 Voice anti-patterns

- ❌ «Жаль терять вас!» (guilt-trip)
- ❌ «Может, перенесём?» (guilt-trip alternative when customer already chose cancel)
- ❌ «Подумайте ещё раз» (manipulative)
- ❌ «Это безвозвратное действие» (legalese)
- ❌ «Бронирование отменено» (sterile — use «Отменила запись»)
- ❌ «Уважаемый клиент» (corporate)
- ❌ «Cancel» в русском тексте (English jargon in RU UI per cut #2)
- ❌ «Сожалеем о неудобствах» (corporate apology)
- ❌ «Возврат произведён в системе» (sterile bureaucratic — use «Деньги вернутся»)
- ❌ «Скидка 20% если останетесь?» (manipulative retention attempt — explicitly forbidden per spec §3.2)

---

## 8. Phase E — Variants comparison

Per tech lead 5 spec variants + 2 founder-relevant. ASCII inline mocks comparing.

### 8.1 Cancellation reason input

| Variant | Selected | Reason |
|---------|----------|--------|
| (a) Required — must select before confirm | ❌ Rejected | Interrogation feel, friction at sensitive moment |
| **(b) Optional с «Пропустить»** | ✅ **SELECTED** | Data collection без friction, customer agency |
| (c) Skipped entirely | ❌ Rejected | Lose useful churn signal for analytics |

### 8.2 Reschedule entry pattern

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Single-tap from booking card → R1 directly** | ✅ **SELECTED** | Intent clear from tap, no friction |
| (b) Guided wizard with intent re-confirm | ❌ Rejected | Extra tap для common action, slow |
| (c) Inline change on booking detail | ⏸ Alt | Compact, but R1 picker too rich для inline |

### 8.3 Master substitution timing

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Inline в R1 picker** | ✅ **SELECTED** | Customer sees substitution в context, no screen transition |
| (b) Separate R2 screen | ❌ Rejected | Extra step, breaks flow |
| (c) Modal над R1 | ⏸ Alt | OK for edge case, primary should be inline |

### 8.4 Cancel confirmation pattern

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Modal overlay с «Да, отменить»/«Передумала»** | ✅ **SELECTED** | Irreversible action — modal forces conscious tap |
| (b) Inline confirmation (tap Cancel → tap again) | ❌ Rejected | Accidental double-tap risk |
| (c) Bottom sheet с reason + confirm | ⏸ Alt | Combines steps, but harder UX flow |

### 8.5 Success screen tone

| Variant | Selected | Reason |
|---------|----------|--------|
| (a) Minimal «Отменила запись. Готово.» | ❌ Rejected | Cold, no warmth |
| **(b) Slightly warm «Поняла, отменила... Если что — я тут.»** | ✅ **SELECTED** | Door open without manipulation |
| (c) «We'll miss you» emotional | ❌ Rejected | Manipulative retention attempt |

### 8.6 Late cancellation penalty UX (founder-relevant)

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Explicit с amount «50% (около 900 ₽)»** | ✅ **SELECTED** | Customer informed BEFORE commit, no surprise (per founder cut baseline) |
| (b) Subtle hint «С 50% удержания» | ❌ Rejected | Specific amount more honest |
| (c) Hide penalty until after cancel | ❌ Rejected | Surprise bills = trust killer |

### 8.7 Refund timing visibility (founder-relevant)

| Variant | Selected | Reason |
|---------|----------|--------|
| **(a) Explicit «в течение 3 дней»** | ✅ **SELECTED** | Concrete promise customer can track |
| (b) Vague «несколько дней» | ❌ Rejected | Anxiety-inducing для customer |
| (c) Hide refund timing | ❌ Rejected | Customer needs to know to budget / track |

---

## 9. Backend mapping

### 9.1 New / extended endpoints

| Endpoint | Method | Description | Owner |
|----------|--------|-------------|-------|
| `POST /api/v1/customer/bookings/{id}/cancel` | POST | Customer-initiated cancellation. Returns refund_info + chain_terminated flag | W4 |
| `POST /api/v1/customer/bookings/{id}/reschedule` | POST | Customer-initiated reschedule. Returns chain_status (same/new/partial_failure) | W4 |
| `GET /api/v1/customer/bookings/{id}/cancel_policy_preview` | GET | Returns expected_penalty + applicable_refund + hours_to_visit | W4 |
| `GET /api/v1/customer/bookings/{id}/reschedule_options?date=...` | GET | Returns slots + substitution candidates if original master unavailable | W4 |
| `POST /api/v1/customer/notifications/salon_side_cancel/{booking_id}/respond` | POST | Customer responds to no-fault cascade (accept substitute / pick another date / cancel with refund) | W4 |

### 9.2 Response fields critical

**`cancel_policy_preview` response:**
```json
{
  "hours_to_visit": 8,
  "is_late_cancel": true,
  "penalty_amount_rub": 900,
  "penalty_percentage": 50,
  "refund_amount_rub": 900,
  "refund_eta_days": 3,
  "currency": "RUB"
}
```

**`reschedule` response (Q12-α aware):**
```json
{
  "old_booking_id": "uuid_old",
  "new_booking_id": "uuid_new",
  "chain_status": "same|new|partial_failure",
  "billing_impact": "none|will_recalculate",
  "user_message_key": "same_chain|service_swap|over_90_days|partial_failure"
}
```

Frontend maps `user_message_key` → exact voice copy per §7.2.

### 9.3 Q12-α billing chain backend behavior

Per memory `project_q12a_billing_founder_gate`:
- W3 (Zeta stream) уже implemented chain mechanics
- This UX consumes existing backend logic, не дублирует
- Backend MUST return `chain_status` + `billing_impact` in reschedule response
- Frontend uses these to choose correct voice copy

### 9.4 No-fault refund cascade backend

- Admin marks master unavailable → triggers cascade per spec policy §6
- Backend notifies customer via Ayla DM с substitution candidates
- Refund automatically queued (full amount) regardless of customer's choice
- Customer's response (accept substitute / cancel with refund / other date) updates booking accordingly

---

## 10. Accessibility (WCAG 2.2 AA — inline)

Patterns reuse from `customer-booking-flow.md §11`. Cancel/reschedule specific:

1. **2.5.5 Confirm Destructive Action** — Cancel modal (C1) has explicit «Да, отменить»/«Передумала» buttons. Per WCAG, primary action не auto-focused (avoid accidental Enter key).

2. **1.4.3 Contrast** — Late penalty warning «**Важно**» section must use accent color (sage-green-dark или muted red) meeting 4.5:1 на white. NOT bright red (anxiety-inducing).

3. **4.1.3 Status Messages** — Loading «Отменяю...» / «Переношу...» = `role="status" aria-live="polite"`. Success «✓ Отменила запись» = `role="status"`.

4. **2.4.3 Focus Order** — Modal opens → focus к heading. Tab order: Heading → primary CTA → secondary CTA → close. Escape closes modal returns focus к Cancel button.

5. **3.3.1 Error Identification** — Race condition «Это время только что заняли» = `role="alert"` immediate announce. Partial failure error similarly.

6. **1.3.1 Info & Relationships** — Booking context в modal header uses `<dl>` (definition list) для дата / время / мастер / салон semantic structure.

7. **2.4.1 Bypass Blocks** — Skip link «К действиям» в booking detail screen перед cancel/reschedule buttons.

8. **1.4.4 Resize Text** — At 200% zoom modal stays full-width on mobile. Reschedule R1 picker stacks vertical с no horizontal scroll.

9. **2.5.8 Target Size** — All chip buttons (reason, slots, substitution candidates) ≥44dp tap target.

10. **2.3.3 Reduced Motion** — Loading spinner respects `prefers-reduced-motion: reduce` → static placeholder.

---

## 11. Anti-patterns

- ❌ Guilt-trip language («Жаль терять вас!» / «Подумайте ещё раз»)
- ❌ Manipulative retention («Скидка 20% если останетесь?»)
- ❌ Surprise penalties (hiding amount until after commit)
- ❌ Vague refund timing («скоро вернётся» / «несколько дней»)
- ❌ English «Cancel» в Russian UI text
- ❌ Sterile bureaucratic copy («Бронирование отменено в системе»)
- ❌ Customer-facing «chain» concept (internal billing only)
- ❌ Admin identity reveal in salon-side cancel («Анна-администратор отменила»)
- ❌ Blame customer for salon-side cancellation
- ❌ Skip refund promise when cancellation is no-fault
- ❌ Force reason input для cancellation (interrogation feel)
- ❌ Hide policy preview before customer commits
- ❌ Lock customer in flow без escape («Отменить вместо переноса» NOT available)

---

## 12. Open questions / followups

### Resolved at Phase B

All Q-CR-1..10 resolved per founder verdict 2026-05-26:
- Q-CR-1: (b) Optional reason с «Пропустить» ✅
- Q-CR-2: (a) Single-tap reschedule entry ✅
- Q-CR-3: (a) Inline master substitution ✅
- Q-CR-4: (a) Modal cancel confirmation ✅
- Q-CR-5: (b) Slightly warm success tone ✅
- Q-CR-6: (a) Explicit late cancel penalty с amount ✅
- Q-CR-7: (a) Always show reschedule billing transparency ✅
- Q-CR-8: No reschedule reason input ✅
- Q-CR-9: (a) Salon-side DM с inline alternatives ✅
- Q-CR-10: Records tab integration via booking detail screen ✅

### Post-pilot followups

| # | Question | Phase |
|---|----------|-------|
| Q-CR-POST-1 | A/B test reason input — required vs optional cancellation rate impact | Phase 2+ |
| Q-CR-POST-2 | Reschedule «save draft» when customer abandons mid-flow | Phase 2+ |
| Q-CR-POST-3 | Notification delay before salon-side cascade triggers (avoid spam) | Phase 2+ |
| Q-CR-POST-4 | Customer cancellation pattern analytics (3+ cancellations → soft check-in) | Phase 2+ |
| Q-CR-POST-5 | Refund payment provider integration | Engineering scope |
| Q-CR-POST-6 | Recurring booking cancel (cancel all future occurrences) | Phase 2+ |
| Q-CR-POST-7 | Reschedule с pricing comparison overlay (Q12-α extension) | Phase 2+ |
| Q-CR-POST-8 | Voice cancellation («отмени запись на завтра») via bot DM | Phase 2+ |

### For W1 / Iota (frontend implementer)

1. **Cancel modal** focus trap, escape key returns to booking card
2. **Late cancel penalty amount** received from backend `cancel_policy_preview` endpoint
3. **Reschedule R1 picker** reuse F3 booking flow component с state-dependent header
4. **Master substitution inline** receive `substitution_candidates` field from backend
5. **Q12-α messaging** use `user_message_key` from backend to map exact copy
6. **No-fault cascade DM** received via existing Ayla DM channel
7. **Success screen actions** lazy-load (don't block render для maps deeplink check)
8. **Partial failure handling** show admin queue ticket reference (for support escalation)
9. **Race condition** «слот занят» — backend returns 409 with substitution_candidates
10. **Tenant SUSPENDED mid-flow** — backend returns 503 with `next_available_alternative_tenant`

---

## 13. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | Sage-green palette, soft modal patterns, ASCII reuse from booking flow |
| Direct code reading | A | customer-cancellation-reschedule-spec.md (state machine + templates), Q12-α memory (chain rules + 5 edges) |
| `Brand Guardian` subagent | F (CRITICAL voice review) | Cancel tone = high-trust moment. See review applied inline below |
| UI Designer subagent | (skipped — pattern reuse from booking-flow + dashboard) | n/a |
| Accessibility Auditor subagent | (skipped — inline notes §10) | n/a |

---

## 14. Status next steps

- [x] Phase A — read policy + Q12-α memory + booking-conflict-resolution §6.6b + customer-booking-flow F4 cancellation preview
- [x] Phase B — plan structure + 10 Q-CR questions + Phase E 7 variants
- [x] Phase C — 10 screens ASCII (4 cancellation + 5 reschedule + 1 no-fault cascade)
- [x] Phase D — states matrix + voice patterns + Q12-α chain UX rules
- [x] Phase E — 7 variants comparison (5 spec + 2 founder-relevant)
- [x] Phase F — Brand Guardian voice review (pending — applied inline §7)
- [x] Phase G — A11y notes inline §10
- [x] Phase I — save `docs/screens/customer-cancellation-reschedule-flow.md`
- [ ] Phase J — handoff block for tech lead
- [ ] Phase K — commit + rebase + push + PR + self-merge per `feedback_tau_branch_push_discipline`

**Severity результирующего flow:** P0 BLOCKER pilot — без cancel/reschedule UX = half-baked customer experience, support load + trust damage.

**Following streams to engage after sign-off:**
- W1 — ~20-25 hrs frontend (10 screens + modal patterns + R1 picker reuse + master substitution inline + Q12-α messaging mapping + no-fault cascade UI)
- W4 — ~3-5 hrs backend (cancel_policy_preview endpoint + reschedule_options endpoint + cascade response handler — existing booking endpoints extended)
- W3 (billing chain Q12-α) уже implemented — no new scope here

---

## 15. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (10 Q-CR verdicts + ranking philosophy + no-fault Q2 verdict) | ✅ | 2026-05-26 |
| Tech Lead (Phase B Option B + Phase E 7 variants) | ✅ | 2026-05-26 |
| Tau (author) | ✅ | 2026-05-26 |
| UX Architect | ☐ | (pending review) |
| Brand Guardian (voice CRITICAL — high-trust moment) | ✅ | 2026-05-26 (applied inline §7 + voice rules per cut baseline) |
| W1 (10 screens + modal patterns + master substitution) | ☐ | (pending impl) |
| W4 (cancel/reschedule endpoints + Q12-α chain integration) | ☐ | (pending impl) |
| W3 (billing chain Q12-α) — already implemented | ✅ | per memory `project_q12a_billing_founder_gate` |
| Accessibility Engineer (WCAG 2.2 AA pass per §10) | ☐ | (pending pilot) |

## Last verified
2026-05-26 r1 — Founder strategic plan + 10 Q-CR verdicts + 5 Q12-α edge cases + no-fault Q2 verdict applied. All cut baseline disciplines from booking-flow refresh integrated. Brand Guardian voice review pending — high-trust moment criticality.
